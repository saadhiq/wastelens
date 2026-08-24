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

    # Object storage
    s3_endpoint_url: str = "http://localhost:9000"
    # Host used when signing URLs handed to the browser (Phase 3 review page).
    # In Docker, s3_endpoint_url is the container-network hostname ("minio"),
    # which a browser on the host can't resolve — this is the address it can
    # actually reach. Defaults to s3_endpoint_url for non-Docker setups where
    # the two coincide.
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
