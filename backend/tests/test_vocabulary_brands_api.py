"""Vocabulary/brand CRUD and unmapped-label promotion (Phase 3). DB-backed,
uses the `client` TestClient against the real API."""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import BagType, UnmappedLabel, UnmappedLabelKind
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


class TestVocabularyApi:
    def test_analyst_can_list_but_not_create(self, client, analyst_account):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        listing = client.get("/api/v1/vocabulary", headers=headers)
        assert listing.status_code == 200

        create = client.post(
            "/api/v1/vocabulary",
            headers=headers,
            json={
                "bag_type": "organic",
                "item_name": f"x-{uuid.uuid4().hex[:6]}",
                "display_name": "X",
            },
        )
        assert create.status_code == 403

    def test_admin_full_crud(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        name = f"test_item_{uuid.uuid4().hex[:8]}"

        create = client.post(
            "/api/v1/vocabulary",
            headers=headers,
            json={"bag_type": "organic", "item_name": name, "display_name": "Test Item"},
        )
        assert create.status_code == 201, create.text
        item_id = create.json()["id"]

        update = client.patch(
            f"/api/v1/vocabulary/{item_id}", headers=headers, json={"active": False}
        )
        assert update.status_code == 200
        assert update.json()["active"] is False

        delete = client.delete(f"/api/v1/vocabulary/{item_id}", headers=headers)
        assert delete.status_code == 204
        assert client.get(f"/api/v1/vocabulary/{item_id}", headers=headers).status_code == 404

    def test_duplicate_bag_type_item_name_rejected(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        name = f"dup_item_{uuid.uuid4().hex[:8]}"
        body = {"bag_type": "paper", "item_name": name, "display_name": "Dup"}
        first = client.post("/api/v1/vocabulary", headers=headers, json=body)
        assert first.status_code == 201
        second = client.post("/api/v1/vocabulary", headers=headers, json=body)
        assert second.status_code == 409


class TestPromoteUnmappedLabel:
    def test_promote_creates_vocabulary_item_and_resolves_label(self, client, admin_account, db):
        label = UnmappedLabel(raw_label=f"mystery_{uuid.uuid4().hex[:8]}", bag_type=BagType.organic)
        db.add(label)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.post(f"/api/v1/vocabulary/from-unmapped/{label.id}", headers=headers, json={})
        assert resp.status_code == 201, resp.text
        assert resp.json()["item_name"] == label.raw_label
        assert resp.json()["bag_type"] == "organic"

        db.refresh(label)
        assert label.resolved is True
        assert label.suggested_vocabulary_item_id == uuid.UUID(resp.json()["id"])

    def test_promote_twice_rejected(self, client, admin_account, db):
        label = UnmappedLabel(raw_label=f"mystery_{uuid.uuid4().hex[:8]}", bag_type=BagType.organic)
        db.add(label)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        first = client.post(
            f"/api/v1/vocabulary/from-unmapped/{label.id}", headers=headers, json={}
        )
        assert first.status_code == 201
        second = client.post(
            f"/api/v1/vocabulary/from-unmapped/{label.id}", headers=headers, json={}
        )
        assert second.status_code == 409

    def test_reviewer_cannot_promote(self, client, reviewer_account, db):
        label = UnmappedLabel(raw_label=f"mystery_{uuid.uuid4().hex[:8]}", bag_type=BagType.organic)
        db.add(label)
        db.commit()

        headers = login(client, reviewer_account["email"], reviewer_account["password"])
        resp = client.post(f"/api/v1/vocabulary/from-unmapped/{label.id}", headers=headers, json={})
        assert resp.status_code == 403

    def test_reviewer_can_list_unmapped_labels(self, client, reviewer_account):
        headers = login(client, reviewer_account["email"], reviewer_account["password"])
        resp = client.get("/api/v1/vocabulary/unmapped", headers=headers)
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_brand_kind_label_cannot_be_promoted(self, client, admin_account, db):
        label = UnmappedLabel(
            raw_label=f"BrandX-{uuid.uuid4().hex[:8]}",
            bag_type=BagType.polythene,
            label_kind=UnmappedLabelKind.BRAND,
        )
        db.add(label)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.post(f"/api/v1/vocabulary/from-unmapped/{label.id}", headers=headers, json={})
        assert resp.status_code == 409

    def test_default_listing_excludes_brand_kind(self, client, admin_account, db):
        item_label = UnmappedLabel(
            raw_label=f"item-{uuid.uuid4().hex[:8]}", bag_type=BagType.organic
        )
        brand_label = UnmappedLabel(
            raw_label=f"brand-{uuid.uuid4().hex[:8]}",
            bag_type=BagType.polythene,
            label_kind=UnmappedLabelKind.BRAND,
        )
        db.add_all([item_label, brand_label])
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/vocabulary/unmapped?limit=200", headers=headers)
        assert resp.status_code == 200
        labels = [row["raw_label"] for row in resp.json()["items"]]
        assert item_label.raw_label in labels
        assert brand_label.raw_label not in labels

        explicit_brand = client.get(
            "/api/v1/vocabulary/unmapped?label_kind=BRAND&limit=200", headers=headers
        )
        assert explicit_brand.status_code == 200
        brand_labels = [row["raw_label"] for row in explicit_brand.json()["items"]]
        assert brand_label.raw_label in brand_labels
        assert item_label.raw_label not in brand_labels


class TestBrandsApi:
    def test_analyst_can_list_but_not_create(self, client, analyst_account):
        headers = login(client, analyst_account["email"], analyst_account["password"])
        assert client.get("/api/v1/brands", headers=headers).status_code == 200
        resp = client.post(
            "/api/v1/brands", headers=headers, json={"name": f"Brand-{uuid.uuid4().hex[:6]}"}
        )
        assert resp.status_code == 403

    def test_admin_full_crud(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        name = f"Brand-{uuid.uuid4().hex[:8]}"

        create = client.post(
            "/api/v1/brands", headers=headers, json={"name": name, "aliases": ["alt"]}
        )
        assert create.status_code == 201, create.text
        brand_id = create.json()["id"]

        update = client.patch(
            f"/api/v1/brands/{brand_id}", headers=headers, json={"category": "snacks"}
        )
        assert update.status_code == 200
        assert update.json()["category"] == "snacks"

        delete = client.delete(f"/api/v1/brands/{brand_id}", headers=headers)
        assert delete.status_code == 204

    def test_duplicate_name_rejected(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        name = f"Brand-{uuid.uuid4().hex[:8]}"
        first = client.post("/api/v1/brands", headers=headers, json={"name": name})
        assert first.status_code == 201
        second = client.post("/api/v1/brands", headers=headers, json={"name": name})
        assert second.status_code == 409
