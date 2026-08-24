"""Phase 4 operations surface: pickups, inspection stations, bins (+
transfer), collectors, calendar, and bag weigh-in."""

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    Bag,
    BagType,
    Bin,
    BinType,
    CalendarDay,
    PickupChannel,
    PickupRequest,
    Resident,
    StaffAccount,
    StaffRole,
)
from app.seeds.seed import seed_calendar_days
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
    r = Resident(name="Ops Test", phone=f"+9470{suffix[:7]}", address="x")
    db.add(r)
    db.commit()
    return r


class TestResidentLookup:
    def test_collector_can_find_by_phone(self, client, collector_account, resident):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.get(f"/api/v1/users/by-phone/{resident.phone}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == str(resident.id)

    def test_unknown_phone_404(self, client, collector_account):
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.get("/api/v1/users/by-phone/+9999999999", headers=headers)
        assert resp.status_code == 404

    def test_find_by_qr(self, client, collector_account, resident, db):
        resident.qr_code = f"QR-{uuid.uuid4().hex[:8]}"
        db.commit()
        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.get(f"/api/v1/users/by-qr/{resident.qr_code}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == str(resident.id)

    def test_analyst_cannot_lookup_pii(self, client, analyst_account, resident):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.get(f"/api/v1/users/by-phone/{resident.phone}", headers=headers)
        assert resp.status_code == 403


class TestGetBag:
    def test_returns_bag(self, client, station_operator_account, resident, db):
        bag = Bag(
            user_id=resident.id, bag_type=BagType.organic, tag_id=f"GB-{uuid.uuid4().hex[:8]}"
        )
        db.add(bag)
        db.commit()

        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        resp = client.get(f"/api/v1/bags/{bag.id}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["gross_weight_kg"] is None

    def test_unknown_bag_404(self, client, station_operator_account):
        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        resp = client.get(f"/api/v1/bags/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404


class TestPickups:
    def test_book_cancel_and_miss(self, client, station_operator_account, resident):
        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        book = client.post(
            "/api/v1/pickups",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "requested_for_date": "2026-09-10",
                "channel": "PHONE",
            },
        )
        assert book.status_code == 201, book.text
        pickup_id = book.json()["id"]
        assert book.json()["status"] == "REQUESTED"

        listing = client.get(
            "/api/v1/pickups", headers=headers, params={"date": "2026-09-10", "status": "REQUESTED"}
        )
        assert listing.status_code == 200
        assert any(p["id"] == pickup_id for p in listing.json()["items"])

        cancel = client.patch(
            f"/api/v1/pickups/{pickup_id}/cancel",
            headers=headers,
            json={"reason": "resident asked"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "CANCELLED"

        cancel_again = client.patch(f"/api/v1/pickups/{pickup_id}/cancel", headers=headers, json={})
        assert cancel_again.status_code == 409

    def test_miss(self, client, collector_account, resident, db):
        pickup = PickupRequest(
            resident_id=resident.id,
            requested_for_date=dt.date(2026, 9, 11),
            channel=PickupChannel.PHONE,
        )
        db.add(pickup)
        db.commit()

        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.post(f"/api/v1/pickups/{pickup.id}/miss", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "MISSED"

    def test_analyst_cannot_book(self, client, analyst_account, resident):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.post(
            "/api/v1/pickups",
            headers=headers,
            json={
                "user_id": str(resident.id),
                "requested_for_date": "2026-09-10",
                "channel": "IVR",
            },
        )
        assert resp.status_code == 403

    def test_analyst_can_list(self, client, analyst_account):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.get("/api/v1/pickups", headers=headers)
        assert resp.status_code == 200


class TestStations:
    def test_admin_full_crud(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        code = f"ST-{uuid.uuid4().hex[:6]}"
        create = client.post(
            "/api/v1/stations",
            headers=headers,
            json={"station_code": code, "facility_name": "Main"},
        )
        assert create.status_code == 201, create.text
        station_id = create.json()["id"]

        update = client.patch(
            f"/api/v1/stations/{station_id}", headers=headers, json={"line_name": "Line A"}
        )
        assert update.status_code == 200
        assert update.json()["line_name"] == "Line A"

        delete = client.delete(f"/api/v1/stations/{station_id}", headers=headers)
        assert delete.status_code == 204

    def test_duplicate_station_code_rejected(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        code = f"ST-{uuid.uuid4().hex[:6]}"
        body = {"station_code": code, "facility_name": "Main"}
        assert client.post("/api/v1/stations", headers=headers, json=body).status_code == 201
        assert client.post("/api/v1/stations", headers=headers, json=body).status_code == 409

    def test_station_operator_cannot_create(self, client, station_operator_account):
        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        resp = client.post(
            "/api/v1/stations",
            headers=headers,
            json={"station_code": f"ST-{uuid.uuid4().hex[:6]}", "facility_name": "Main"},
        )
        assert resp.status_code == 403


class TestBinsAndTransfer:
    def test_admin_crud(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        code = f"BIN-{uuid.uuid4().hex[:6]}"
        create = client.post(
            "/api/v1/bins", headers=headers, json={"bin_code": code, "bin_type": "ORGANIC"}
        )
        assert create.status_code == 201, create.text
        bin_id = create.json()["id"]

        update = client.patch(
            f"/api/v1/bins/{bin_id}", headers=headers, json={"vendor_name": "Acme"}
        )
        assert update.status_code == 200
        assert update.json()["vendor_name"] == "Acme"

    def test_duplicate_bin_code_rejected(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        code = f"BIN-{uuid.uuid4().hex[:6]}"
        body = {"bin_code": code, "bin_type": "PAPER"}
        assert client.post("/api/v1/bins", headers=headers, json=body).status_code == 201
        assert client.post("/api/v1/bins", headers=headers, json=body).status_code == 409

    def test_station_operator_can_transfer(self, client, station_operator_account, resident, db):
        bin_ = Bin(bin_code=f"BIN-{uuid.uuid4().hex[:6]}", bin_type=BinType.GENERAL)
        bag = Bag(
            user_id=resident.id, bag_type=BagType.general, tag_id=f"TR-{uuid.uuid4().hex[:8]}"
        )
        db.add_all([bin_, bag])
        db.commit()

        headers = login(
            client, station_operator_account["email"], station_operator_account["password"]
        )
        resp = client.post(
            "/api/v1/bins/transfer",
            headers=headers,
            json={"bag_id": str(bag.id), "bin_id": str(bin_.id), "weight_kg": "3.20"},
        )
        assert resp.status_code == 201, resp.text
        db.refresh(bag)
        assert bag.assigned_bin_id == bin_.id

    def test_analyst_cannot_transfer(self, client, analyst_account, resident, db):
        bin_ = Bin(bin_code=f"BIN-{uuid.uuid4().hex[:6]}", bin_type=BinType.GENERAL)
        bag = Bag(
            user_id=resident.id, bag_type=BagType.general, tag_id=f"TR2-{uuid.uuid4().hex[:8]}"
        )
        db.add_all([bin_, bag])
        db.commit()

        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.post(
            "/api/v1/bins/transfer",
            headers=headers,
            json={"bag_id": str(bag.id), "bin_id": str(bin_.id)},
        )
        assert resp.status_code == 403


class TestCollectors:
    def test_admin_creates_collector_for_collector_role_account(self, client, admin_account, db):
        staff = StaffAccount(
            email=f"col-{uuid.uuid4().hex[:8]}@wastelens-test.io",
            full_name="Field Staff",
            hashed_password="x",
            role=StaffRole.collector,
        )
        db.add(staff)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.post(
            "/api/v1/collectors",
            headers=headers,
            json={
                "staff_account_id": str(staff.id),
                "employee_code": f"EMP-{uuid.uuid4().hex[:6]}",
                "full_name": staff.full_name,
            },
        )
        assert resp.status_code == 201, resp.text

    def test_wrong_role_staff_account_rejected(
        self, client, admin_account, station_operator_account
    ):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.post(
            "/api/v1/collectors",
            headers=headers,
            json={
                "staff_account_id": station_operator_account["id"],
                "employee_code": f"EMP-{uuid.uuid4().hex[:6]}",
                "full_name": "Wrong Role",
            },
        )
        assert resp.status_code == 409


class TestCalendar:
    def test_seed_is_idempotent(self, db):
        today = dt.date(2026, 6, 1)
        first_pass = seed_calendar_days(db, today=today)
        db.commit()
        assert first_pass > 0

        second_pass = seed_calendar_days(db, today=today)
        db.commit()
        assert second_pass == 0

        jan1 = db.get(CalendarDay, dt.date(2026, 1, 1))
        assert jan1 is not None
        assert jan1.is_poya is False
        assert jan1.is_public_holiday is False

    def test_admin_can_edit_poya_flag(self, client, admin_account, db):
        day = dt.date(2027, 3, 15)
        if db.get(CalendarDay, day) is None:
            db.add(CalendarDay(calendar_date=day, day_of_week=day.weekday(), is_weekend=False))
            db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.patch(
            f"/api/v1/calendar/{day.isoformat()}",
            headers=headers,
            json={"is_poya": True, "note": "Full moon"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_poya"] is True
        assert resp.json()["note"] == "Full moon"

    def test_non_admin_cannot_edit(self, client, analyst_account, db):
        day = dt.date(2027, 4, 1)
        if db.get(CalendarDay, day) is None:
            db.add(CalendarDay(calendar_date=day, day_of_week=day.weekday(), is_weekend=False))
            db.commit()

        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.patch(
            f"/api/v1/calendar/{day.isoformat()}", headers=headers, json={"is_public_holiday": True}
        )
        assert resp.status_code == 403

    def test_unseeded_date_404(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.patch("/api/v1/calendar/1999-01-01", headers=headers, json={"is_poya": True})
        assert resp.status_code == 404


class TestBagWeigh:
    def test_collector_can_weigh(self, client, collector_account, resident, db):
        bag = Bag(
            user_id=resident.id, bag_type=BagType.organic, tag_id=f"WG-{uuid.uuid4().hex[:8]}"
        )
        db.add(bag)
        db.commit()

        headers = login(client, collector_account["email"], collector_account["password"])
        resp = client.patch(
            f"/api/v1/bags/{bag.id}/weigh",
            headers=headers,
            json={"gross_weight_kg": "3.00", "tare_weight_kg": "0.50", "bag_condition": "GOOD"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["gross_weight_kg"] == "3.00"
        assert body["net_weight_kg"] == "2.50"

    def test_analyst_cannot_weigh(self, client, analyst_account, resident, db):
        bag = Bag(
            user_id=resident.id, bag_type=BagType.organic, tag_id=f"WG2-{uuid.uuid4().hex[:8]}"
        )
        db.add(bag)
        db.commit()

        headers = login(client, analyst_account["email"], analyst_account["password"])
        resp = client.patch(
            f"/api/v1/bags/{bag.id}/weigh", headers=headers, json={"gross_weight_kg": "1.00"}
        )
        assert resp.status_code == 403
