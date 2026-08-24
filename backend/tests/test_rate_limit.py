"""Phase 7: fixed-window rate limiting — the primitive itself, and its
wiring onto POST /captures."""

import io
import uuid

import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import Bag, BagType, Resident
from app.services.rate_limit import RateLimitExceeded, check_rate_limit
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


class TestCheckRateLimit:
    def test_allows_up_to_the_limit(self):
        key = f"test-{uuid.uuid4().hex}"
        assert check_rate_limit(key, limit=3, window_seconds=60) == 1
        assert check_rate_limit(key, limit=3, window_seconds=60) == 2
        assert check_rate_limit(key, limit=3, window_seconds=60) == 3

    def test_raises_once_over_the_limit(self):
        key = f"test-{uuid.uuid4().hex}"
        check_rate_limit(key, limit=1, window_seconds=60)
        with pytest.raises(RateLimitExceeded):
            check_rate_limit(key, limit=1, window_seconds=60)

    def test_different_keys_are_independent(self):
        key_a = f"test-a-{uuid.uuid4().hex}"
        key_b = f"test-b-{uuid.uuid4().hex}"
        check_rate_limit(key_a, limit=1, window_seconds=60)
        # key_b has never been used — shouldn't be affected by key_a.
        assert check_rate_limit(key_b, limit=1, window_seconds=60) == 1


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(1, 2, 3)).save(buf, format="JPEG")
    return buf.getvalue()


class TestCaptureUploadRateLimit:
    def test_429_after_configured_limit(self, client, station_operator_account, db, monkeypatch):
        monkeypatch.setattr("app.services.storage.upload_image", lambda *a, **kw: None)
        monkeypatch.setattr("app.api.v1.captures.enqueue_analysis", lambda capture_id: None)
        monkeypatch.setattr(get_settings(), "capture_upload_rate_limit", 1)
        monkeypatch.setattr(get_settings(), "capture_upload_rate_window_seconds", 60)

        suffix = uuid.uuid4().hex[:8]
        resident = Resident(name="Rate Limit Test", phone=f"+9482{suffix[:7]}", address="x")
        db.add(resident)
        db.flush()
        bag_a = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"RL-A-{suffix}")
        bag_b = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"RL-B-{suffix}")
        db.add_all([bag_a, bag_b])
        db.commit()

        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )

        first = client.post(
            "/api/v1/captures",
            headers=headers,
            files={"image": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
            data={"bag_tag_id": bag_a.tag_id, "station_id": "st-rl"},
        )
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/v1/captures",
            headers=headers,
            files={"image": ("b.jpg", _jpeg_bytes(), "image/jpeg")},
            data={"bag_tag_id": bag_b.tag_id, "station_id": "st-rl"},
        )
        assert second.status_code == 429
