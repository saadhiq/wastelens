"""Health probe tests. Liveness needs nothing; readiness reports per-dependency
status (and may be degraded in unit-test environments — that's expected)."""


def test_liveness(bare_client):
    resp = bare_client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readiness_reports_all_checks(bare_client):
    resp = bare_client.get("/api/v1/health/ready")
    body = resp.json()
    assert set(body["checks"]) == {"database", "redis", "object_storage"}
    assert resp.status_code in (200, 503)


def test_error_envelope_shape(bare_client):
    resp = bare_client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert {"code", "message"} <= set(body["error"])
    assert "request_id" in body


def test_request_id_header_echoed(bare_client):
    resp = bare_client.get("/api/v1/health/live", headers={"x-request-id": "test-123"})
    assert resp.headers["x-request-id"] == "test-123"
