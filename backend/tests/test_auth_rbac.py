"""Integration tests: login, refresh, staff creation, and PII role-gating.
Skipped automatically when no test database is reachable."""

import pytest

from tests.conftest import login, requires_db

pytestmark = requires_db


def test_login_and_me(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_login_wrong_password(client, admin_account):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": admin_account["email"], "password": "nope"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "http_401"


def test_refresh_flow(client, admin_account):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": admin_account["email"], "password": admin_account["password"]},
    )
    refresh_token = resp.json()["refresh_token"]
    resp2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp2.status_code == 200
    assert resp2.json()["access_token"]


def test_admin_creates_staff(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.post(
        "/api/v1/auth/staff",
        headers=headers,
        json={
            "email": "op1@wastelens-test.io",
            "full_name": "Operator One",
            "password": "op-pass-123",
            "role": "station_operator",
        },
    )
    assert resp.status_code in (201, 409)  # 409 if re-run against same DB


def test_analyst_cannot_create_staff(client, analyst_account):
    headers = login(client, analyst_account["email"], analyst_account["password"])
    resp = client.post(
        "/api/v1/auth/staff",
        headers=headers,
        json={
            "email": "x@wastelens-test.io",
            "full_name": "X",
            "password": "x-pass-123",
            "role": "reviewer",
        },
    )
    assert resp.status_code == 403


@pytest.fixture()
def resident_id(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={"name": "Test Resident", "phone": "+94771234567", "address": "1 Test Lane"},
    )
    if resp.status_code == 409:  # phone exists from a previous run
        listing = client.get("/api/v1/users", headers=headers).json()
        return listing["items"][0]["id"]
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_analyst_gets_anonymized_listing_only(client, analyst_account, resident_id):
    headers = login(client, analyst_account["email"], analyst_account["password"])

    listing = client.get("/api/v1/users", headers=headers)
    assert listing.status_code == 200
    for item in listing.json()["items"]:
        assert "phone" not in item and "name" not in item  # anonymized shape

    detail = client.get(f"/api/v1/users/{resident_id}", headers=headers)
    assert detail.status_code == 403  # PII detail is role-gated


def test_admin_reads_pii_detail(client, admin_account, resident_id):
    headers = login(client, admin_account["email"], admin_account["password"])
    detail = client.get(f"/api/v1/users/{resident_id}", headers=headers)
    assert detail.status_code == 200
    assert "phone" in detail.json()


def test_invalid_phone_rejected(client, admin_account):
    headers = login(client, admin_account["email"], admin_account["password"])
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={"name": "Bad Phone", "phone": "abc", "address": "nowhere"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_unauthenticated_request_rejected(client):
    resp = client.get("/api/v1/users")
    assert resp.status_code == 401
