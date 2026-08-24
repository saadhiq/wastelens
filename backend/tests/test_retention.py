"""Phase 7: image retention job — deletes S3 images past the configured
window, keeps every derived row, is idempotent."""

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Bag, BagType, Capture, CollectionSession, Detection, Resident
from app.services.retention import purge_old_images
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _capture(db, *, captured_at: dt.datetime, purged: bool = False) -> Capture:
    suffix = uuid.uuid4().hex[:10]
    resident = Resident(name="Retention Test", phone=f"+9481{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"RET-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=BagType.organic,
        image_url=f"captures/{suffix}.jpg",
        station_id="st-ret",
        captured_at=captured_at,
        image_purged_at=captured_at if purged else None,
    )
    db.add(capture)
    db.flush()
    db.add(Detection(capture_id=capture.id, item_name="banana_peel", confidence=0.9))
    db.commit()
    return capture


@pytest.fixture(autouse=True)
def _mock_storage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.retention.storage.delete_image", lambda key: calls.append(key)
    )
    return calls


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class TestPurgeOldImages:
    def test_purges_images_past_the_window(self, db, _mock_storage):
        old_capture = _capture(db, captured_at=_now() - dt.timedelta(days=200))
        purged = purge_old_images(db, now=_now())
        assert purged >= 1
        assert old_capture.image_url in _mock_storage

        db.refresh(old_capture)
        assert old_capture.image_purged_at is not None

    def test_leaves_recent_images_alone(self, db, _mock_storage):
        recent_capture = _capture(db, captured_at=_now() - dt.timedelta(days=5))
        purge_old_images(db, now=_now())
        assert recent_capture.image_url not in _mock_storage
        db.refresh(recent_capture)
        assert recent_capture.image_purged_at is None

    def test_does_not_touch_derived_rows(self, db, _mock_storage):
        old_capture = _capture(db, captured_at=_now() - dt.timedelta(days=200))
        purge_old_images(db, now=_now())

        db.refresh(old_capture)
        assert old_capture.bag_type == BagType.organic  # capture row untouched
        detections = db.query(Detection).filter_by(capture_id=old_capture.id).all()
        assert len(detections) == 1
        assert detections[0].item_name == "banana_peel"

    def test_idempotent_skips_already_purged(self, db, _mock_storage):
        already_purged = _capture(db, captured_at=_now() - dt.timedelta(days=200), purged=True)
        purge_old_images(db, now=_now())
        assert already_purged.image_url not in _mock_storage
