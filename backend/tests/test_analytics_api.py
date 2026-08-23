"""Analytics endpoint tests: role gating and response shapes."""

from tests.conftest import login, requires_db

pytestmark = requires_db


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
    } <= set(body)


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
