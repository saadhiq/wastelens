"""Phase 7: CSV/PDF exports for analysts (profiles, top items, top brands,
quality). Each export goes through the same hard consent/is_sensitive gate
as the Phase 6 consumption layer."""

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
    UserWasteProfile,
)
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _resident(db, *, consent: bool) -> Resident:
    suffix = uuid.uuid4().hex[:8]
    r = Resident(
        name="Export Test", phone=f"+9479{suffix[:7]}", address="x", consent_profiling=consent
    )
    db.add(r)
    db.commit()
    return r


def _detection(db, resident: Resident, *, item_name: str = "banana_peel") -> None:
    suffix = uuid.uuid4().hex[:10]
    bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"EXP-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=BagType.organic,
        image_url=f"captures/{suffix}.jpg",
        station_id="st-exp",
    )
    db.add(capture)
    db.flush()
    db.add(
        Detection(capture_id=capture.id, item_name=item_name, category="organic", confidence=0.9)
    )
    db.commit()


class TestTopItemsExport:
    def test_csv_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/top-items?format=csv", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.text.splitlines()[0] == "item_name,count"

    def test_pdf_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/top-items?format=pdf", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_invalid_format_rejected(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/top-items?format=xml", headers=headers)
        assert resp.status_code == 422

    def test_export_excludes_non_consenting_resident(self, client, admin_account, db):
        consenting = _resident(db, consent=True)
        non_consenting = _resident(db, consent=False)
        unique_item = f"exportonly_{uuid.uuid4().hex[:8]}"
        _detection(db, consenting, item_name=unique_item)
        _detection(db, non_consenting, item_name=unique_item)

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(
            "/api/v1/analytics/top-items?format=csv&days=365&limit=50", headers=headers
        )
        assert resp.status_code == 200
        line = next(
            (row for row in resp.text.splitlines() if row.startswith(unique_item + ",")), None
        )
        assert line is not None, resp.text
        # Only the consenting resident's detection should count, not both.
        assert line == f"{unique_item},1"

    def test_export_requires_analyst_role(self, client):
        assert client.get("/api/v1/analytics/top-items?format=csv").status_code == 401


class TestTopBrandsExport:
    def test_csv_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/top-brands?format=csv", headers=headers)
        assert resp.status_code == 200
        assert resp.text.splitlines()[0] == "brand,count"


class TestQualityExport:
    def test_csv_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/quality?format=csv", headers=headers)
        assert resp.status_code == 200
        assert resp.text.splitlines()[0] == "item_name,detections,avg_confidence,reviewed,corrected"

    def test_pdf_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/quality?format=pdf", headers=headers)
        assert resp.status_code == 200
        assert resp.content.startswith(b"%PDF")


class TestProfileExport:
    def test_404_for_non_consenting_resident(self, client, admin_account, db):
        resident = _resident(db, consent=False)
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{resident.id}?format=csv", headers=headers)
        assert resp.status_code == 404

    def test_404_for_unknown_resident(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{uuid.uuid4()}?format=pdf", headers=headers)
        assert resp.status_code == 404

    def test_csv_export_for_consenting_resident(self, client, admin_account, db):
        resident = _resident(db, consent=True)
        db.add(
            UserWasteProfile(
                user_id=resident.id,
                week_start="2026-08-17",
                veg_frequency=5,
                packaged_food_frequency=2,
            )
        )
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{resident.id}?format=csv", headers=headers)
        assert resp.status_code == 200, resp.text
        assert "2026-08-17" in resp.text
        assert "5" in resp.text

    def test_json_path_unchanged_for_non_consenting_resident(self, client, admin_account, db):
        """The plain JSON GET is deliberately NOT retrofitted with the
        Phase 6 gate — only exports are. Documents the intentional
        divergence (see DECISIONS.md)."""
        resident = _resident(db, consent=False)
        db.add(UserWasteProfile(user_id=resident.id, week_start="2026-08-17", veg_frequency=3))
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{resident.id}", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
