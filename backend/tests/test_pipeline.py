"""Integration test: capture → analyze → detections, with a mocked
VisionProvider and in-memory object storage. Requires the test database
(skipped otherwise); Redis and MinIO are faked."""

import json
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    AnalysisStatus,
    Bag,
    BagStatus,
    BagType,
    Brand,
    Capture,
    CollectionSession,
    Detection,
    InferenceRun,
    InferenceRunStatus,
    Resident,
    UnmappedLabel,
    UnmappedLabelKind,
    VocabularyItem,
)
from app.services.analysis import analyze_capture
from app.services.brand_match import match_brand
from app.services.vision.base import ProviderResponse, VisionProvider
from tests.conftest import requires_db

pytestmark = requires_db


class FakeProvider(VisionProvider):
    """Scripted provider: returns queued responses in order."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def analyze(self, image_bytes, media_type, bag_type, vocabulary, prior_invalid_output=None):
        self.calls += 1
        return ProviderResponse(
            raw_text=self._responses.pop(0), model="fake", latency_ms=1, usage={}
        )


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def capture_fixture(db, monkeypatch):
    """A pending capture with vocabulary, a brand, and fake storage/redis."""
    suffix = uuid.uuid4().hex[:8]
    resident = Resident(name="P", phone=f"+9477{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    bag = Bag(user_id=resident.id, bag_type=BagType.polythene, tag_id=f"TAG-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    for name in ("chips_packet", "unidentified_item"):
        if (
            db.query(VocabularyItem).filter_by(bag_type=BagType.polythene, item_name=name).first()
            is None
        ):
            db.add(VocabularyItem(bag_type=BagType.polythene, item_name=name, display_name=name))
    if db.query(Brand).filter_by(name="Munchee").first() is None:
        db.add(Brand(name="Munchee", aliases=["munchee biscuits"], category="biscuits"))
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=BagType.polythene,
        image_url="captures/test.jpg",
        station_id="st-1",
        analysis_status=AnalysisStatus.pending,
    )
    db.add(capture)
    db.commit()

    monkeypatch.setattr(
        "app.services.analysis.storage.download_image",
        lambda key: (b"fake-image-bytes", "image/jpeg"),
    )
    monkeypatch.setattr("app.services.analysis.register_cv_call", lambda: 1)
    return capture


GOOD_RESPONSE = json.dumps(
    {
        "detections": [
            {
                "item_name": "chips_packet",
                "confidence": 0.93,
                "ocr_text": "MUNCHEE Super Cream Cracker",
                "estimated_quantity": "1",
            },
            {"item_name": "mystery_thing", "confidence": 0.4},
        ],
        "tray_notes": "packaging waste",
    }
)


def test_happy_path_stores_detections_and_flags_review(db, capture_fixture):
    provider = FakeProvider([GOOD_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    capture = db.get(Capture, capture_fixture.id)
    assert capture.analysis_status == AnalysisStatus.done
    assert db.get(Bag, capture.bag_id).status == BagStatus.processed

    detections = db.query(Detection).filter_by(capture_id=capture.id).all()
    assert len(detections) == 2

    by_conf = {round(d.confidence, 2): d for d in detections}
    high = by_conf[0.93]
    assert high.item_name == "chips_packet"
    assert high.needs_review is False
    assert high.matched_brand_id is not None  # "MUNCHEE" fuzzy-matched

    low = by_conf[0.4]
    # Out-of-vocabulary name demoted; original preserved in subcategory.
    assert low.item_name == "unidentified_item"
    assert "mystery_thing" in (low.subcategory or "")
    assert low.needs_review is True  # 0.4 < 0.75 threshold


def test_repair_round_recovers_bad_json(db, capture_fixture):
    provider = FakeProvider(["oops, not json", GOOD_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)
    assert provider.calls == 2
    assert db.get(Capture, capture_fixture.id).analysis_status == AnalysisStatus.done


def test_two_failures_mark_capture_failed(db, capture_fixture):
    provider = FakeProvider(["garbage one", "garbage two"])
    analyze_capture(db, capture_fixture.id, provider=provider)
    capture = db.get(Capture, capture_fixture.id)
    assert capture.analysis_status == AnalysisStatus.failed
    # Raw output preserved for debugging.
    rows = db.query(Detection).filter_by(capture_id=capture.id).all()
    assert any((r.raw_model_output or {}).get("error") == "unparseable_model_output" for r in rows)


def test_brand_matching(db, capture_fixture):
    assert match_brand(db, "MUNCHEE super cracker 100g") is not None
    assert match_brand(db, "completely unrelated text zzz") is None
    assert match_brand(db, None) is None
    assert match_brand(db, "   ") is None


# --- Phase 5: packaging extraction (brand/product/pack-size/barcode/material) ---

V2_RESPONSE = json.dumps(
    {
        "detections": [
            {
                "item_name": "chips_packet",
                "confidence": 0.91,
                "brand_text": "Munchee",
                "product_name_text": "Super Cream Cracker",
                "pack_size_text": "100g",
                "barcode_text": "8901030826501",
                "material_type": "BOPP plastic film",
            },
            {
                "item_name": "chips_packet",
                "confidence": 0.85,
                "brand_text": "TotallyUnknownBrandXYZ",
                "material_type": "LDPE film",
            },
        ],
        "tray_notes": None,
    }
)


def test_packaging_fields_stored_on_detection(db, capture_fixture):
    provider = FakeProvider([V2_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    detections = (
        db.query(Detection)
        .filter_by(capture_id=capture_fixture.id)
        .order_by(Detection.confidence)
        .all()
    )
    assert len(detections) == 2
    known = detections[1]
    assert known.brand_text == "Munchee"
    assert known.product_name_text == "Super Cream Cracker"
    assert known.pack_size_text == "100g"
    assert known.material_type == "BOPP plastic film"
    assert known.matched_brand_id is not None


def test_unmatched_brand_recorded_as_unmapped_label(db, capture_fixture):
    # A brand text unique to this test — other tests in this file also
    # process V2_RESPONSE's "TotallyUnknownBrandXYZ" against the same
    # (non-rolled-back) DB session, so asserting an exact occurrence_count
    # against that shared literal would be order-dependent. See DECISIONS.md
    # / the Phase 2 test-scoping lesson this mirrors.
    unique_brand = f"UnknownBrand-{uuid.uuid4().hex[:8]}"
    response = json.dumps(
        {
            "detections": [
                {"item_name": "chips_packet", "confidence": 0.85, "brand_text": unique_brand}
            ],
            "tray_notes": None,
        }
    )
    provider = FakeProvider([response])
    analyze_capture(db, capture_fixture.id, provider=provider)

    unmapped = (
        db.query(UnmappedLabel)
        .filter_by(raw_label=unique_brand, label_kind=UnmappedLabelKind.BRAND)
        .one_or_none()
    )
    assert unmapped is not None
    assert unmapped.bag_type == BagType.polythene
    assert unmapped.occurrence_count == 1

    # Seeing the same unmatched brand again increments, not duplicates.
    from app.services.brand_match import record_unmapped_brand

    record_unmapped_brand(db, unique_brand, BagType.polythene)
    db.commit()
    db.refresh(unmapped)
    assert unmapped.occurrence_count == 2


def test_barcode_source_recorded_when_no_decode_pass_finds_anything(db, capture_fixture):
    # capture_fixture's image bytes are fake ("fake-image-bytes"), so
    # decode_barcodes always returns [] here — this exercises the
    # falls-back-to-model-OCR path, not the decoded-wins path (that's
    # covered by services/barcode.py's own tests against real images).
    provider = FakeProvider([V2_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    detections = (
        db.query(Detection)
        .filter_by(capture_id=capture_fixture.id)
        .order_by(Detection.confidence)
        .all()
    )
    known = detections[1]  # brand_text="Munchee", the one with barcode_text set
    assert known.barcode_text == "8901030826501"
    assert known.raw_model_output["barcode_source"] == "ocr"

    unknown = detections[0]  # no barcode_text from the model at all
    assert unknown.barcode_text is None
    assert unknown.raw_model_output["barcode_source"] is None


def test_prompt_version_stamped_on_inference_run(db, capture_fixture):
    """capture_fixture is a polythene bag — Phase 5's v2 packaging contract."""
    provider = FakeProvider([V2_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    run = db.query(InferenceRun).filter_by(capture_id=capture_fixture.id).one()
    assert run.prompt_version == "v2"


# --- Phase 2: InferenceRun bookkeeping ---


class RaisingProvider(VisionProvider):
    """Simulates a network/provider-level failure — analyze() raises instead
    of returning malformed text."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0

    def analyze(self, image_bytes, media_type, bag_type, vocabulary, prior_invalid_output=None):
        self.calls += 1
        raise self._exc


def test_malformed_first_attempt_produces_two_inference_runs(db, capture_fixture):
    provider = FakeProvider(["oops, not json", GOOD_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    runs = (
        db.query(InferenceRun)
        .filter_by(capture_id=capture_fixture.id)
        .order_by(InferenceRun.attempt_no)
        .all()
    )
    assert len(runs) == 2
    assert runs[0].attempt_no == 1
    assert runs[0].status == InferenceRunStatus.FAILED_INVALID_JSON
    assert runs[0].raw_text == "oops, not json"
    assert runs[1].attempt_no == 2
    assert runs[1].status == InferenceRunStatus.SUCCESS


def test_detections_link_to_the_successful_attempt_not_the_failed_one(db, capture_fixture):
    provider = FakeProvider(["oops, not json", GOOD_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    attempt_2 = db.query(InferenceRun).filter_by(capture_id=capture_fixture.id, attempt_no=2).one()
    detections = db.query(Detection).filter_by(capture_id=capture_fixture.id).all()
    assert len(detections) == 2
    assert all(d.inference_run_id == attempt_2.id for d in detections)


def test_rerunning_a_done_capture_is_a_noop(db, capture_fixture):
    provider = FakeProvider([GOOD_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)
    assert provider.calls == 1

    run_count_before = db.query(InferenceRun).filter_by(capture_id=capture_fixture.id).count()
    detection_count_before = db.query(Detection).filter_by(capture_id=capture_fixture.id).count()

    # Re-run with a provider that would error if actually called — proves
    # the early-return guard fires before any new attempt is made.
    analyze_capture(
        db, capture_fixture.id, provider=RaisingProvider(RuntimeError("should not run"))
    )

    assert (
        db.query(InferenceRun).filter_by(capture_id=capture_fixture.id).count() == run_count_before
    )
    assert (
        db.query(Detection).filter_by(capture_id=capture_fixture.id).count()
        == detection_count_before
    )


def test_provider_error_recorded_as_its_own_inference_run_status(db, capture_fixture):
    provider = RaisingProvider(ConnectionError("connection reset"))
    with pytest.raises(ConnectionError):
        analyze_capture(db, capture_fixture.id, provider=provider)

    run = db.query(InferenceRun).filter_by(capture_id=capture_fixture.id).one()
    assert run.attempt_no == 1
    assert run.status == InferenceRunStatus.FAILED_PROVIDER_ERROR
    assert "connection reset" in run.error_message
    assert db.get(Capture, capture_fixture.id).analysis_status == AnalysisStatus.failed


def test_retry_after_provider_error_continues_at_next_attempt_no(db, capture_fixture):
    """A Celery retry re-enters analyze_capture for the same capture after a
    prior attempt already wrote an InferenceRun row. attempt_no must advance,
    not collide with the existing row (see _next_attempt_no)."""
    with pytest.raises(ConnectionError):
        analyze_capture(db, capture_fixture.id, provider=RaisingProvider(ConnectionError("boom")))

    provider = FakeProvider([GOOD_RESPONSE])
    analyze_capture(db, capture_fixture.id, provider=provider)

    runs = (
        db.query(InferenceRun)
        .filter_by(capture_id=capture_fixture.id)
        .order_by(InferenceRun.attempt_no)
        .all()
    )
    assert [r.attempt_no for r in runs] == [1, 2]
    assert runs[0].status == InferenceRunStatus.FAILED_PROVIDER_ERROR
    assert runs[1].status == InferenceRunStatus.SUCCESS
    assert db.get(Capture, capture_fixture.id).analysis_status == AnalysisStatus.done
