"""POST /sessions (Phase 4): nested collector/vehicle/route/GPS + bags in
one request, bag identity resolution (existing vs. auto-created tags,
mismatched resident/type conflicts), pickup-request fulfillment linkage,
and PATCH /sessions/{id}/arrive."""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    Bag,
    BagType,
    Collector,
    PickupChannel,
    PickupRequest,
    PickupStatus,
    Resident,
    StaffAccount,
)
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def resident(db):
    suffix = uuid.uuid4().hex[:8]
    r = Resident(name="Collector Test", phone=f"+9478{suffix[:7]}", address="x")
    db.add(r)
    db.commit()
    return r


@pytest.fixture()
def collector_profile(db, collector_account):
    staff = db.get(StaffAccount, uuid.UUID(collector_account["id"]))
    collector = Collector(
        staff_account_id=staff.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:6]}",
        full_name=staff.full_name,
    )
    db.add(collector)
    db.commit()
    return collector


class TestCreateSession:
    def test_creates_session_with_new_bags(self, client, collector_account, resident):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "vehicle_code": "VAN-1",
                "route_code": "R-9",
                "gps_latitude": "6.927100",
                "gps_longitude": "79.861200",
                "bags": [
                    {"bag_type": "organic", "gross_weight_kg": "2.50", "bag_condition": "GOOD"},
                    {"bag_type": "paper", "tag_id": f"TAG-{uuid.uuid4().hex[:8]}"},
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["vehicle_code"] == "VAN-1"
        assert len(body["bags"]) == 2
        organic = next(b for b in body["bags"] if b["bag_type"] == "organic")
        assert organic["gross_weight_kg"] == "2.50"
        assert organic["bag_condition"] == "GOOD"
        assert organic["status"] == "collected"
        assert organic["tag_id"]  # server-generated

    def test_reuses_existing_bag_by_tag(self, client, collector_account, resident, db):
        existing = Bag(
            user_id=resident.id, bag_type=BagType.polythene, tag_id=f"EXIST-{uuid.uuid4().hex[:8]}"
        )
        db.add(existing)
        db.commit()

        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "bags": [
                    {"bag_type": "polythene", "tag_id": existing.tag_id, "gross_weight_kg": "1.10"}
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["bags"][0]["id"] == str(existing.id)
        db.refresh(existing)
        assert str(existing.gross_weight_kg) == "1.10"

    def test_tag_belonging_to_different_resident_rejected(
        self, client, collector_account, resident, db
    ):
        other = Resident(name="Other", phone=f"+9479{uuid.uuid4().hex[:7]}", address="y")
        db.add(other)
        db.flush()
        theirs = Bag(
            user_id=other.id, bag_type=BagType.general, tag_id=f"THEIRS-{uuid.uuid4().hex[:8]}"
        )
        db.add(theirs)
        db.commit()

        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "bags": [{"bag_type": "general", "tag_id": theirs.tag_id}],
            },
        )
        assert resp.status_code == 409

    def test_tag_with_mismatched_type_rejected(self, client, collector_account, resident, db):
        existing = Bag(
            user_id=resident.id, bag_type=BagType.organic, tag_id=f"MISM-{uuid.uuid4().hex[:8]}"
        )
        db.add(existing)
        db.commit()

        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "bags": [{"bag_type": "paper", "tag_id": existing.tag_id}],
            },
        )
        assert resp.status_code == 409

    def test_unknown_resident_404(self, client, collector_account):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions", headers=headers, json={"user_id": str(uuid.uuid4()), "bags": []}
        )
        assert resp.status_code == 404

    def test_fulfilling_a_pickup_marks_it_completed(self, client, collector_account, resident, db):
        pickup = PickupRequest(
            resident_id=resident.id,
            requested_for_date="2026-09-01",
            channel=PickupChannel.PHONE,
            status=PickupStatus.REQUESTED,
        )
        db.add(pickup)
        db.commit()

        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"user_id": str(resident.id), "pickup_request_id": str(pickup.id), "bags": []},
        )
        assert resp.status_code == 201, resp.text
        session_id = resp.json()["id"]

        db.refresh(pickup)
        assert pickup.status == PickupStatus.COMPLETED
        assert str(pickup.collection_session_id) == session_id

    def test_explicit_collector_id_linked(
        self, client, collector_account, collector_profile, resident
    ):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "collector_id": str(collector_profile.id),
                "bags": [],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["collector_id"] == str(collector_profile.id)

    def test_unknown_collector_id_404(self, client, collector_account, resident):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"user_id": str(resident.id), "collector_id": str(uuid.uuid4()), "bags": []},
        )
        assert resp.status_code == 404

    def test_analyst_cannot_create_session(self, client, analyst_account, resident):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.post(
            "/api/v1/sessions", headers=headers, json={"user_id": str(resident.id), "bags": []}
        )
        assert resp.status_code == 403


class TestArriveSession:
    def test_sets_warehouse_arrival(self, client, collector_account, resident):
        headers = login(client, collector_account["email"], collector_account["password"])
        create = client.post(
            "/api/v1/sessions", headers=headers, json={"user_id": str(resident.id), "bags": []}
        )
        session_id = create.json()["id"]
        assert create.json()["warehouse_arrival_at"] is None

        arrive = client.patch(f"/api/v1/sessions/{session_id}/arrive", headers=headers, json={})
        assert arrive.status_code == 200, arrive.text
        assert arrive.json()["warehouse_arrival_at"] is not None

    def test_unknown_session_404(self, client, collector_account):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.patch(f"/api/v1/sessions/{uuid.uuid4()}/arrive", headers=headers, json={})
        assert resp.status_code == 404
