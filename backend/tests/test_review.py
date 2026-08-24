"""Review workflow tests (Phase 3): the queue rule, apply_review, stats, and
the API endpoints' role gating. DB-backed.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import (
    AnalysisStatus,
    Bag,
    BagType,
    Brand,
    Capture,
    CollectionSession,
    Detection,
    Resident,
    ReviewStatus,
    ReviewVerdict,
    StaffAccount,
    StaffRole,
)
from app.schemas.review import ReviewAction
from app.services.review import (
    apply_review,
    build_review_queue,
    compute_review_stats,
)
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _capture(db, bag_type=BagType.organic) -> Capture:
    suffix = uuid.uuid4().hex[:8]
    resident = Resident(name="R", phone=f"+9479{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    bag = Bag(user_id=resident.id, bag_type=bag_type, tag_id=f"REV-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=bag_type,
        image_url=f"captures/{suffix}.jpg",
        station_id="s1",
        analysis_status=AnalysisStatus.done,
    )
    db.add(capture)
    db.commit()
    return capture


def _reviewer(db) -> StaffAccount:
    account = StaffAccount(
        email=f"r-{uuid.uuid4().hex[:8]}@wastelens-test.io",
        full_name="Test Reviewer",
        hashed_password="not-a-real-hash",
        role=StaffRole.reviewer,
    )
    db.add(account)
    db.flush()
    return account


def _queue_ids(db) -> list[uuid.UUID]:
    return [row["detection"].id for row in build_review_queue(db, limit=200, offset=0)]


class TestQueueRule:
    def test_low_confidence_included(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 0)
        capture = _capture(db)
        d = Detection(capture_id=capture.id, item_name="carrot", confidence=0.4, category="organic")
        db.add(d)
        db.commit()
        assert d.id in _queue_ids(db)

    def test_unidentified_item_included_regardless_of_confidence(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 0)
        capture = _capture(db)
        d = Detection(
            capture_id=capture.id,
            item_name="unidentified_item",
            confidence=0.95,
            category="organic",
        )
        db.add(d)
        db.commit()
        assert d.id in _queue_ids(db)

    def test_unmatched_brand_text_included(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 0)
        capture = _capture(db, bag_type=BagType.polythene)
        d = Detection(
            capture_id=capture.id,
            item_name="chips_packet",
            confidence=0.95,
            category="polythene",
            brand_text="Totally Unknown Co",
        )
        db.add(d)
        db.commit()
        assert d.id in _queue_ids(db)

    def test_high_confidence_matched_excluded_without_qa_sample(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 0)
        capture = _capture(db, bag_type=BagType.polythene)
        brand = Brand(name=f"KnownBrand-{uuid.uuid4().hex[:6]}", aliases=[])
        db.add(brand)
        db.flush()
        d = Detection(
            capture_id=capture.id,
            item_name="chips_packet",
            confidence=0.95,
            category="polythene",
            brand_text="Known Brand",
            matched_brand_id=brand.id,
        )
        db.add(d)
        db.commit()
        assert d.id not in _queue_ids(db)

    def test_qa_sample_can_include_high_confidence(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 100)
        capture = _capture(db)
        d = Detection(
            capture_id=capture.id, item_name="carrot", confidence=0.95, category="organic"
        )
        db.add(d)
        db.commit()
        assert d.id in _queue_ids(db)

    def test_already_reviewed_excluded(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 0)
        capture = _capture(db)
        d = Detection(
            capture_id=capture.id,
            item_name="carrot",
            confidence=0.2,
            category="organic",
            review_status=ReviewStatus.confirmed,
        )
        db.add(d)
        db.commit()
        assert d.id not in _queue_ids(db)

    def test_ordered_oldest_capture_first(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "review_qa_sample_percent", 0)
        old_capture = _capture(db)
        db.query(Capture).filter_by(id=old_capture.id).update(
            {"captured_at": dt.datetime.now(dt.UTC) - dt.timedelta(days=2)}
        )
        new_capture = _capture(db)
        d_old = Detection(
            capture_id=old_capture.id, item_name="carrot", confidence=0.1, category="organic"
        )
        d_new = Detection(
            capture_id=new_capture.id, item_name="carrot", confidence=0.1, category="organic"
        )
        db.add_all([d_old, d_new])
        db.commit()
        ids = _queue_ids(db)
        assert ids.index(d_old.id) < ids.index(d_new.id)


class TestApplyReview:
    def test_confirm(self, db):
        capture = _capture(db)
        d = Detection(
            capture_id=capture.id,
            item_name="carrot",
            confidence=0.4,
            category="organic",
            needs_review=True,
        )
        db.add(d)
        reviewer = _reviewer(db)
        db.commit()

        review = apply_review(
            db, detection=d, reviewer=reviewer, action=ReviewAction(verdict=ReviewVerdict.CONFIRMED)
        )

        db.refresh(d)
        assert d.review_status == ReviewStatus.confirmed
        assert d.needs_review is False
        assert d.reviewed_by == reviewer.id
        assert d.item_name == "carrot"  # original never touched
        assert d.corrected_item_name is None
        assert review.verdict == ReviewVerdict.CONFIRMED

    def test_correct_sets_corrected_item_name_without_touching_original(self, db):
        capture = _capture(db)
        d = Detection(
            capture_id=capture.id, item_name="carrot_top", confidence=0.4, category="organic"
        )
        db.add(d)
        reviewer = _reviewer(db)
        db.commit()

        apply_review(
            db,
            detection=d,
            reviewer=reviewer,
            action=ReviewAction(verdict=ReviewVerdict.CORRECTED, corrected_item_name="onion_peel"),
        )

        db.refresh(d)
        assert d.review_status == ReviewStatus.corrected
        assert d.corrected_item_name == "onion_peel"
        assert d.item_name == "carrot_top"

    def test_reject_leaves_corrected_item_name_unset(self, db):
        capture = _capture(db)
        d = Detection(
            capture_id=capture.id, item_name="egg_shell", confidence=0.95, category="organic"
        )
        db.add(d)
        reviewer = _reviewer(db)
        db.commit()

        apply_review(
            db, detection=d, reviewer=reviewer, action=ReviewAction(verdict=ReviewVerdict.REJECTED)
        )

        db.refresh(d)
        assert d.review_status == ReviewStatus.rejected
        assert d.corrected_item_name is None

    def test_creates_audit_log_entry(self, db):
        from app.models import AuditLog

        capture = _capture(db)
        d = Detection(capture_id=capture.id, item_name="carrot", confidence=0.4, category="organic")
        db.add(d)
        reviewer = _reviewer(db)
        db.commit()

        apply_review(
            db, detection=d, reviewer=reviewer, action=ReviewAction(verdict=ReviewVerdict.CONFIRMED)
        )

        assert (
            db.query(AuditLog)
            .filter_by(actor_id=reviewer.id, action="detection.review", entity_id=str(d.id))
            .count()
            == 1
        )


class TestReviewStats:
    def test_counts_and_agreement_rate_scoped_to_one_reviewer(self, db):
        capture = _capture(db)
        reviewer = _reviewer(db)
        detections = [
            Detection(capture_id=capture.id, item_name="x", confidence=0.5, category="organic")
            for _ in range(3)
        ]
        db.add_all(detections)
        db.commit()

        apply_review(
            db,
            detection=detections[0],
            reviewer=reviewer,
            action=ReviewAction(verdict=ReviewVerdict.CONFIRMED),
        )
        apply_review(
            db,
            detection=detections[1],
            reviewer=reviewer,
            action=ReviewAction(verdict=ReviewVerdict.CONFIRMED),
        )
        apply_review(
            db,
            detection=detections[2],
            reviewer=reviewer,
            action=ReviewAction(verdict=ReviewVerdict.REJECTED),
        )

        stats = compute_review_stats(db)
        # Scoped to this reviewer — other tests in this session may have
        # their own reviewers/reviews in the same shared db_engine.
        row = next(s for s in stats.by_reviewer if s.reviewer_id == reviewer.id)
        assert row.reviewed_count == 3
        assert row.confirmed_count == 2
        assert row.rejected_count == 1
        assert row.corrected_count == 0


class TestReviewApi:
    def test_station_operator_cannot_review(self, client, station_operator_account, db):
        capture = _capture(db)
        d = Detection(capture_id=capture.id, item_name="carrot", confidence=0.4, category="organic")
        db.add(d)
        db.commit()

        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        resp = client.post(
            f"/api/v1/detections/{d.id}/review", headers=headers, json={"verdict": "CONFIRMED"}
        )
        assert resp.status_code == 403

    def test_reviewer_can_confirm_via_api(self, client, reviewer_account, db):
        capture = _capture(db)
        d = Detection(capture_id=capture.id, item_name="carrot", confidence=0.4, category="organic")
        db.add(d)
        db.commit()

        headers = login(client, reviewer_account["email"], reviewer_account["password"])
        resp = client.post(
            f"/api/v1/detections/{d.id}/review", headers=headers, json={"verdict": "CONFIRMED"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["review_status"] == "confirmed"

    def test_correct_without_a_correction_field_rejected(self, client, reviewer_account, db):
        capture = _capture(db)
        d = Detection(capture_id=capture.id, item_name="carrot", confidence=0.4, category="organic")
        db.add(d)
        db.commit()

        headers = login(client, reviewer_account["email"], reviewer_account["password"])
        resp = client.post(
            f"/api/v1/detections/{d.id}/review", headers=headers, json={"verdict": "CORRECTED"}
        )
        assert resp.status_code == 422

    def test_bulk_review_skips_already_reviewed_and_unknown_ids(self, client, reviewer_account, db):
        capture = _capture(db)
        fresh = Detection(capture_id=capture.id, item_name="a", confidence=0.4, category="organic")
        already = Detection(
            capture_id=capture.id, item_name="b", confidence=0.4, category="organic"
        )
        db.add_all([fresh, already])
        reviewer = _reviewer(db)
        db.commit()
        apply_review(
            db,
            detection=already,
            reviewer=reviewer,
            action=ReviewAction(verdict=ReviewVerdict.CONFIRMED),
        )

        unknown_id = uuid.uuid4()
        headers = login(client, reviewer_account["email"], reviewer_account["password"])
        resp = client.post(
            "/api/v1/detections/bulk-review",
            headers=headers,
            json={"detection_ids": [str(fresh.id), str(already.id), str(unknown_id)]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reviewed"] == 1
        assert set(body["skipped"]) == {str(already.id), str(unknown_id)}

    def test_queue_and_stats_endpoints_reachable_by_reviewer(self, client, reviewer_account):
        headers = login(client, reviewer_account["email"], reviewer_account["password"])
        queue_resp = client.get("/api/v1/review/queue", headers=headers)
        assert queue_resp.status_code == 200
        assert "items" in queue_resp.json()

        stats_resp = client.get("/api/v1/review/stats", headers=headers)
        assert stats_resp.status_code == 200
        assert "agreement_rate" in stats_resp.json()

    def test_queue_endpoint_forbidden_for_analyst(self, client, analyst_account):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.get("/api/v1/review/queue", headers=headers)
        assert resp.status_code == 403
