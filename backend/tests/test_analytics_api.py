"""Analytics endpoint tests: role gating and response shapes."""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    AnalysisStatus,
    Bag,
    BagType,
    Capture,
    CollectionSession,
    Detection,
    HumanReview,
    InferenceRun,
    InferenceRunStatus,
    Resident,
    ReviewStatus,
    ReviewVerdict,
    UnmappedLabel,
    UnmappedLabelKind,
)
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def test_analytics_requires_analyst_role(client, admin_account):
    # Unauthenticated -> 401
    assert client.get("/api/v1/analytics/quality").status_code == 401


def test_operator_cannot_read_analytics(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.post(
        "/api/v1/auth/staff",
        headers=headers,
        json={
            "email": "op-analytics@wastelens-test.io",
            "full_name": "Op",
            "password": "op-pass-123",
            "role": "station_operator",
        },
    )
    assert resp.status_code in (201, 409)
    op_headers = login(client, "op-analytics@wastelens-test.io", "op-pass-123")
    assert client.get("/api/v1/analytics/quality", headers=op_headers).status_code == 403


def test_quality_report_shape(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.get("/api/v1/analytics/quality", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert {
        "total_detections",
        "avg_confidence",
        "pct_needs_review",
        "capture_failure_rate",
        "by_item",
        "by_prompt_version",
    } <= set(body)
    assert isinstance(body["by_prompt_version"], list)


def test_unmapped_brands_shape(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.get("/api/v1/analytics/unmapped-brands", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_unmapped_brands_requires_analyst_role(client, admin_account):
    resp = client.get("/api/v1/analytics/unmapped-brands")
    assert resp.status_code == 401


def test_top_items_and_brands(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    items = client.get("/api/v1/analytics/top-items?days=90", headers=headers)
    assert items.status_code == 200
    assert isinstance(items.json(), list)
    brands = client.get("/api/v1/analytics/top-brands?days=90", headers=headers)
    assert brands.status_code == 200


def test_rebuild_and_fetch_profiles(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.post("/api/v1/profiles/rebuild?weeks_back=2", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["weeks_back"] == 2


def test_unmapped_brands_ranked_by_occurrence(client, admin_account, db):
    suffix = uuid.uuid4().hex[:8]
    frequent = f"NewBrand-{suffix}"
    rare = f"OtherBrand-{suffix}"
    db.add_all(
        [
            UnmappedLabel(
                raw_label=frequent,
                bag_type=BagType.polythene,
                label_kind=UnmappedLabelKind.BRAND,
                occurrence_count=9,
            ),
            UnmappedLabel(
                raw_label=rare,
                bag_type=BagType.paper,
                label_kind=UnmappedLabelKind.BRAND,
                occurrence_count=1,
            ),
            # An ITEM-kind row with a huge count must never leak into this
            # report — it answers a different question (vocabulary gaps).
            UnmappedLabel(
                raw_label=f"item-{suffix}",
                bag_type=BagType.organic,
                label_kind=UnmappedLabelKind.ITEM,
                occurrence_count=99,
            ),
        ]
    )
    db.commit()

    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.get("/api/v1/analytics/unmapped-brands?limit=50", headers=headers)
    assert resp.status_code == 200
    labels = [row["raw_label"] for row in resp.json()]
    assert frequent in labels
    assert rare in labels
    assert f"item-{suffix}" not in labels
    assert labels.index(frequent) < labels.index(rare)


class TestQualityByPromptVersion:
    def test_reflects_reviewed_detections(self, client, admin_account, reviewer_account, db):
        suffix = uuid.uuid4().hex[:8]
        resident = Resident(name="QP Test", phone=f"+9476{suffix[:7]}", address="x")
        db.add(resident)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.polythene, tag_id=f"QP-{suffix}")
        session = CollectionSession(user_id=resident.id)
        db.add_all([bag, session])
        db.flush()
        capture = Capture(
            session_id=session.id,
            bag_id=bag.id,
            bag_type=BagType.polythene,
            image_url="captures/qp-test.jpg",
            station_id="st-qp",
            analysis_status=AnalysisStatus.done,
        )
        db.add(capture)
        db.flush()

        run = InferenceRun(
            capture_id=capture.id,
            attempt_no=1,
            provider_name="nvidia",
            model_name="test-model-qp",
            prompt_version="v2",
            status=InferenceRunStatus.SUCCESS,
        )
        db.add(run)
        db.flush()

        detection = Detection(
            capture_id=capture.id,
            item_name="chips_packet",
            confidence=0.9,
            inference_run_id=run.id,
            review_status=ReviewStatus.confirmed,
        )
        db.add(detection)
        db.flush()

        db.add(
            HumanReview(
                detection_id=detection.id,
                reviewer_id=uuid.UUID(reviewer_account["id"]),
                verdict=ReviewVerdict.CONFIRMED,
            )
        )
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/quality?days=365", headers=headers)
        assert resp.status_code == 200
        by_prompt = resp.json()["by_prompt_version"]
        row = next(
            (
                r
                for r in by_prompt
                if r["prompt_version"] == "v2" and r["model_name"] == "test-model-qp"
            ),
            None,
        )
        assert row is not None, by_prompt
        assert row["reviewed"] >= 1
        assert row["confirmed"] >= 1
        assert row["accuracy"] == 1.0


class TestConsumptionEndpoints:
    def test_consumption_404_for_unknown_resident(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{uuid.uuid4()}/consumption", headers=headers)
        assert resp.status_code == 404

    def test_consumption_404_for_non_consenting_resident(self, client, admin_account, db):
        resident = Resident(
            name="No Consent",
            phone=f"+9473{uuid.uuid4().hex[:7]}",
            address="x",
            consent_profiling=False,
        )
        db.add(resident)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{resident.id}/consumption", headers=headers)
        assert resp.status_code == 404

    def test_consumption_shape_for_consenting_resident(self, client, admin_account, db):
        resident = Resident(
            name="Consents",
            phone=f"+9474{uuid.uuid4().hex[:7]}",
            address="x",
            consent_profiling=True,
        )
        db.add(resident)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{resident.id}/consumption", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resident_id"] == str(resident.id)
        assert body["category_signals"] == []
        assert body["brand_signals"] == []
        assert body["packaged_vs_fresh_ratio"] is None
        assert body["spoiled_food_share"] is None

    def test_predictions_empty_list_for_unknown_resident(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get(f"/api/v1/profiles/{uuid.uuid4()}/predictions", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_station_operator_cannot_read_consumption(self, client, admin_account, db):
        resident = Resident(
            name="RBAC Test",
            phone=f"+9475{uuid.uuid4().hex[:7]}",
            address="x",
            consent_profiling=True,
        )
        db.add(resident)
        db.commit()

        headers = login(client, admin_account["email"], admin_account["password"])
        create = client.post(
            "/api/v1/auth/staff",
            headers=headers,
            json={
                "email": f"op-consumption-{uuid.uuid4().hex[:6]}@wastelens-test.io",
                "full_name": "Op",
                "password": "op-pass-123",
                "role": "station_operator",
            },
        )
        assert create.status_code == 201, create.text
        op_headers = login(client, create.json()["email"], "op-pass-123")
        resp = client.get(f"/api/v1/profiles/{resident.id}/consumption", headers=op_headers)
        assert resp.status_code == 403

    def test_brand_switches_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/brand-switches?days=30", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_churn_risk_shape(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/churn-risk", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_brand_switches_requires_analyst_role(self, client):
        assert client.get("/api/v1/analytics/brand-switches").status_code == 401

    def test_churn_risk_requires_analyst_role(self, client):
        assert client.get("/api/v1/analytics/churn-risk").status_code == 401
