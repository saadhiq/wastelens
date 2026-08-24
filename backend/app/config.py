"""Central configuration for WasteLens.

Every tunable (thresholds, model names, caps) lives here and is sourced from the
environment — never hardcoded in business logic. See .env.example for the full list.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    environment: str = "development"
    log_level: str = "INFO"
    api_cors_origins: list[str] = ["http://localhost:5173"]

    # Database / cache
    database_url: str = "postgresql+psycopg://wastelens:change-me@localhost:5432/wastelens"
    redis_url: str = "redis://localhost:6379/0"

    # Object storage. s3_endpoint_url is only for an S3-*compatible* service
    # (MinIO locally/in Docker) — leave it unset (None) to talk to real AWS
    # S3, which boto3 resolves to the correct regional endpoint on its own
    # from s3_region. Setting it always overrides that resolution, so it
    # must be None/absent, not just falsy-ish, for real S3. Defaults to
    # None here (not the MinIO endpoint) — local/Docker dev gets MinIO from
    # .env.example's explicit S3_ENDPOINT_URL=http://minio:9000, so this
    # default is only ever what a real-S3 deployment sees when the line is
    # simply absent from .env.
    s3_endpoint_url: str | None = None
    # Host used when signing URLs handed to the browser (Phase 3 review page).
    # In Docker, s3_endpoint_url is the container-network hostname ("minio"),
    # which a browser on the host can't resolve — this is the address it can
    # actually reach. Defaults to s3_endpoint_url for non-Docker setups where
    # the two coincide. Leave unset along with s3_endpoint_url for real S3 —
    # AWS's presigned URLs are already publicly reachable as-is.
    s3_public_endpoint_url: str | None = None
    s3_access_key: str = "wastelens"
    s3_secret_key: str = "change-me"
    s3_bucket_captures: str = "tray-captures"
    s3_region: str = "us-east-1"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    # NOTE: .local is a special-use TLD rejected by Pydantic's EmailStr — use a real-looking domain.
    bootstrap_admin_email: str = "admin@wastelens.io"
    bootstrap_admin_password: str = "change-me"

    # Vision / CV pipeline
    # Selects the VisionProvider implementation: "nvidia" (default) or "anthropic".
    vision_provider: str = "nvidia"
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_vision_model: str = "meta/llama-3.2-90b-vision-instruct"
    vision_timeout_seconds: int = 180
    anthropic_api_key: str = ""
    vision_model: str = "claude-sonnet-4-6"
    vision_max_tokens: int = 4096
    confidence_review_threshold: float = 0.75
    brand_match_threshold: int = 85
    cv_daily_call_cap: int = 1000

    # Review workflow (Phase 3)
    # % of high-confidence (>= confidence_review_threshold) detections that
    # still land in the review queue as a random QA sample, on top of the
    # detections that need review for other reasons.
    review_qa_sample_percent: int = 5

    # PII encryption (Phase 7 — closes DECISIONS.md #2)
    # Fernet key (32 url-safe base64 bytes). The default below is a
    # generated, non-secret dev key committed to source — fine for local
    # Docker Compose, MUST be overridden (a real secret, out of source
    # control) in any shared/production environment.
    pii_encryption_key: str = "VHrhLtAQA6rrjMZQtJVg2YZfxLTqdWwDi6BM_8KG7Cw="
    # Separate pepper for the phone blind index (HMAC-SHA256) — deliberately
    # not the same key as encryption, so leaking one doesn't compromise both
    # the ciphertext and the lookup index. Same dev-default caveat as above.
    pii_blind_index_key: str = "change-me-blind-index-pepper"

    # Image retention (Phase 7) — see DECISIONS.md for why 90 days.
    image_retention_days: int = 90

    # Rate limiting (Phase 7): capture uploads per station_operator account,
    # fixed-window in Redis.
    capture_upload_rate_limit: int = 60
    capture_upload_rate_window_seconds: int = 60

    # Alerting (Phase 7) — thresholds a scheduled check compares against;
    # exceeding either writes an Alert row (see models/alerts.py).
    alert_failed_run_rate_threshold: float = 0.25
    alert_daily_spend_usd_threshold: float = 20.00


@lru_cache
def get_settings() -> Settings:
    return Settings()
