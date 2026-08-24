"""Phase 7: JSONL training-data export — only human-verified (confirmed/
corrected) detections, gated on consent_operational and is_sensitive,
admin only."""

import json
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    Bag,
    BagType,
    Capture,
    CollectionSession,
    Detection,
    Resident,
    ReviewStatus,
    VocabularyItem,
)
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _resident(db, *, consent_operational: bool = True) -> Resident:
    suffix = uuid.uuid4().hex[:8]
    r = Resident(
        name="Training Export Test",
        phone=f"+9480{suffix[:7]}",
        address="x",
        consent_operational=consent_operational,
    )
    db.add(r)
    db.commit()
    return r


def _capture_with_detections(
    db, resident: Resident, bag_type: BagType, detections: list[dict]
) -> Capture:
    suffix = uuid.uuid4().hex[:10]
    bag = Bag(user_id=resident.id, bag_type=bag_type, tag_id=f"TR-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=bag_type,
        image_url=f"captures/{suffix}.jpg",
        station_id="st-tr",
    )
    db.add(capture)
    db.flush()
    for spec in detections:
        db.add(Detection(capture_id=capture.id, **spec))
    db.commit()
    return capture


def _jsonl_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class TestTrainingExportRBAC:
    def test_requires_admin(self, client, analyst_account):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.get("/api/v1/captures/training-export", headers=headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        assert client.get("/api/v1/captures/training-export").status_code == 401


class TestTrainingExportContent:
    def test_only_confirmed_and_corrected_included(self, client, admin_account, db):
        resident = _resident(db)
        _vocab_organic(db)
        capture = _capture_with_detections(
            db,
            resident,
            BagType.organic,
            [
                {
                    "item_name": "banana_peel",
                    "confidence": 0.9,
                    "review_status": ReviewStatus.confirmed,
                },
                {
                    "item_name": "onion_peel",
                    "confidence": 0.4,
                    "review_status": ReviewStatus.unreviewed,
                },
                {
                    "item_name": "unidentified_item",
                    "confidence": 0.3,
                    "review_status": ReviewStatus.rejected,
                },
            ],
        )

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/captures/training-export", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-ndjson"

        examples = {e["capture_id"]: e for e in _jsonl_lines(resp.text)}
        assert str(capture.id) in examples
        example = examples[str(capture.id)]
        item_names = {d["item_name"] for d in example["detections"]}
        assert item_names == {"banana_peel"}

    def test_corrected_label_uses_correction_not_original(self, client, admin_account, db):
        resident = _resident(db)
        _vocab_organic(db)
        capture = _capture_with_detections(
            db,
            resident,
            BagType.organic,
            [
                {
                    "item_name": "banana_peel",
                    "confidence": 0.5,
                    "review_status": ReviewStatus.corrected,
                    "corrected_item_name": "onion_peel",
                }
            ],
        )

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/captures/training-export", headers=headers)
        examples = {e["capture_id"]: e for e in _jsonl_lines(resp.text)}
        assert examples[str(capture.id)]["detections"][0]["item_name"] == "onion_peel"

    def test_capture_omitted_when_nothing_survives_the_filter(self, client, admin_account, db):
        resident = _resident(db)
        _vocab_organic(db)
        capture = _capture_with_detections(
            db,
            resident,
            BagType.organic,
            [
                {
                    "item_name": "banana_peel",
                    "confidence": 0.9,
                    "review_status": ReviewStatus.rejected,
                }
            ],
        )

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/captures/training-export", headers=headers)
        examples = {e["capture_id"] for e in _jsonl_lines(resp.text)}
        assert str(capture.id) not in examples

    def test_excludes_sensitive_vocabulary_items(self, client, admin_account, db):
        resident = _resident(db)
        if (
            db.query(VocabularyItem)
            .filter_by(bag_type=BagType.organic, item_name="sensitive_med")
            .first()
            is None
        ):
            db.add(
                VocabularyItem(
                    bag_type=BagType.organic,
                    item_name="sensitive_med",
                    display_name="Sensitive med",
                    is_sensitive=True,
                )
            )
            db.commit()
        capture = _capture_with_detections(
            db,
            resident,
            BagType.organic,
            [
                {
                    "item_name": "sensitive_med",
                    "confidence": 0.9,
                    "review_status": ReviewStatus.confirmed,
                }
            ],
        )

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/captures/training-export", headers=headers)
        examples = {e["capture_id"] for e in _jsonl_lines(resp.text)}
        assert str(capture.id) not in examples

    def test_excludes_non_operationally_consenting_resident(self, client, admin_account, db):
        resident = _resident(db, consent_operational=False)
        _vocab_organic(db)
        capture = _capture_with_detections(
            db,
            resident,
            BagType.organic,
            [
                {
                    "item_name": "banana_peel",
                    "confidence": 0.9,
                    "review_status": ReviewStatus.confirmed,
                }
            ],
        )

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/captures/training-export", headers=headers)
        examples = {e["capture_id"] for e in _jsonl_lines(resp.text)}
        assert str(capture.id) not in examples


def _vocab_organic(db) -> None:
    for name in ("banana_peel", "onion_peel"):
        if (
            db.query(VocabularyItem).filter_by(bag_type=BagType.organic, item_name=name).first()
            is None
        ):
            db.add(VocabularyItem(bag_type=BagType.organic, item_name=name, display_name=name))
    db.commit()
