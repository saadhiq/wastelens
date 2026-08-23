"""Liveness and readiness probes.

- /health/live  — process is up (no dependencies checked)
- /health/ready — DB, Redis, and object storage are reachable
"""

from typing import Any

import redis as redis_lib
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness(response: Response) -> dict[str, Any]:
    checks: dict[str, str] = {}
    settings = get_settings()

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — probe must report, not crash
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        r = redis_lib.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    try:
        import boto3
        from botocore.config import Config as BotoConfig

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            # A probe must fail fast, never hang: 2s timeouts, no retries.
            config=BotoConfig(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}),
        )
        s3.list_buckets()
        checks["object_storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["object_storage"] = f"error: {type(exc).__name__}"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
