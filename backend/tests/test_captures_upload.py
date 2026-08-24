"""POST /captures (Phase 4): new upload-time fields (inspection_station_id,
tray_code, lighting_condition), server-computed image_sha256/width/height/
file_size_bytes, and the duplicate-photo-for-the-same-bag 409. Storage and
the Celery enqueue are mocked — this project has no MinIO/Redis service in
CI (see test_pipeline.py's capture_fixture for the same pattern)."""

import io
import uuid

import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.models import Bag, BagType, InspectionStation, Resident
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def resident_and_bag(db):
    suffix = uuid.uuid4().hex[:8]
    resident = Resident(name="Upload Test", phone=f"+9477{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"UP-{suffix}")
    db.add(bag)
    db.commit()
    return resident, bag


@pytest.fixture(autouse=True)
def _mock_storage_and_worker(monkeypatch):
    monkeypatch.setattr("app.services.storage.upload_image", lambda *a, **kw: None)
    monkeypatch.setattr("app.api.v1.captures.enqueue_analysis", lambda capture_id: None)


def _jpeg_bytes(
    size: tuple[int, int] = (24, 16), color: tuple[int, int, int] = (10, 200, 30)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _upload(client, headers, *, tag_id, extra=None, image_bytes=None):
    files = {"image": ("tray.jpg", image_bytes or _jpeg_bytes(), "image/jpeg")}
    data = {"bag_tag_id": tag_id, "station_id": "st-1", **(extra or {})}
    return client.post("/api/v1/captures", headers=headers, files=files, data=data)


class TestCaptureUploadFields:
    def test_new_fields_are_stored_and_computed(
        self, client, station_operator_account, resident_and_bag, db
    ):
        _, bag = resident_and_bag
        station = InspectionStation(station_code=f"ST-{uuid.uuid4().hex[:6]}", facility_name="Main")
        db.add(station)
        db.commit()

        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        image_bytes = _jpeg_bytes(size=(32, 24))
        resp = _upload(
            client,
            headers,
            tag_id=bag.tag_id,
            image_bytes=image_bytes,
            extra={
                "inspection_station_id": str(station.id),
                "tray_code": "TRAY-7",
                "lighting_condition": "OVERHEAD_LED",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["inspection_station_id"] == str(station.id)
        assert body["tray_code"] == "TRAY-7"
        assert body["lighting_condition"] == "OVERHEAD_LED"
        assert body["image_width"] == 32
        assert body["image_height"] == 24
        assert body["file_size_bytes"] == len(image_bytes)
        assert len(body["image_sha256"]) == 64

    def test_unknown_inspection_station_404(
        self, client, station_operator_account, resident_and_bag
    ):
        _, bag = resident_and_bag
        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        resp = _upload(
            client, headers, tag_id=bag.tag_id, extra={"inspection_station_id": str(uuid.uuid4())}
        )
        assert resp.status_code == 404

    def test_duplicate_photo_same_bag_rejected(
        self, client, station_operator_account, resident_and_bag
    ):
        _, bag = resident_and_bag
        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        image_bytes = _jpeg_bytes(color=(1, 2, 3))

        first = _upload(client, headers, tag_id=bag.tag_id, image_bytes=image_bytes)
        assert first.status_code == 201, first.text

        second = _upload(client, headers, tag_id=bag.tag_id, image_bytes=image_bytes)
        assert second.status_code == 409

    def test_same_photo_different_bag_allowed(
        self, client, station_operator_account, resident_and_bag, db
    ):
        resident, bag = resident_and_bag
        other_bag = Bag(
            user_id=resident.id, bag_type=BagType.paper, tag_id=f"UP2-{uuid.uuid4().hex[:8]}"
        )
        db.add(other_bag)
        db.commit()

        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        image_bytes = _jpeg_bytes(color=(9, 9, 9))

        first = _upload(client, headers, tag_id=bag.tag_id, image_bytes=image_bytes)
        assert first.status_code == 201, first.text

        second = _upload(client, headers, tag_id=other_bag.tag_id, image_bytes=image_bytes)
        assert second.status_code == 201, second.text
