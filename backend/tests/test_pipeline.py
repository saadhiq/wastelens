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
    Resident,
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
