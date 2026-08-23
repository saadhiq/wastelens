"""Object storage for tray images (S3-compatible; MinIO locally).

Images are keyed as captures/{capture_id}.{ext}; the DB stores only the key
(as image_url). Download happens in the worker when analysis runs.
"""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import get_settings

_EXT_BY_MEDIA_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

ALLOWED_MEDIA_TYPES = set(_EXT_BY_MEDIA_TYPE)


def _client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
    )


def ensure_bucket() -> None:
    settings = get_settings()
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket_captures)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket_captures)


def object_key(capture_id: str, media_type: str) -> str:
    return f"captures/{capture_id}.{_EXT_BY_MEDIA_TYPE[media_type]}"


def upload_image(key: str, data: bytes, media_type: str) -> None:
    settings = get_settings()
    _client().put_object(
        Bucket=settings.s3_bucket_captures, Key=key, Body=data, ContentType=media_type
    )


def download_image(key: str) -> tuple[bytes, str]:
    """Returns (bytes, media_type)."""
    settings = get_settings()
    obj = _client().get_object(Bucket=settings.s3_bucket_captures, Key=key)
    return obj["Body"].read(), obj.get("ContentType", "image/jpeg")
