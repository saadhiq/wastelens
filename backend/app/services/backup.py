"""Nightly Postgres backup to S3 (Phase 8). pg_dump runs as a subprocess
against DATABASE_URL; the plain-SQL dump is gzipped and uploaded under a
backups/postgres/ key prefix, then backups older than backup_retention_days
are pruned. This is a backup, not a migration path — it only ever reads
from the live database.
"""

import datetime as dt
import gzip
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.core.logging import get_logger
from app.services import storage

log = get_logger(__name__)

BACKUP_PREFIX = "backups/postgres/"


def _pg_dump_dsn(database_url: str) -> str:
    """pg_dump/libpq don't understand the +psycopg SQLAlchemy dialect
    suffix, and a naive string swap isn't enough on its own — a generated
    password containing "/" or other URI-special characters (openssl's
    base64 output can include either) breaks libpq's own URI parser if
    it's not percent-encoded, which a plain f-string/replace won't do.
    SQLAlchemy's URL renderer already handles that encoding correctly."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def run_backup(*, now: dt.datetime | None = None) -> str:
    """Dumps the database, gzips it, uploads to S3, and prunes backups past
    the retention window. Returns the S3 key written."""
    settings = get_settings()
    now = now or dt.datetime.now(dt.UTC)
    bucket = settings.s3_bucket_backups or settings.s3_bucket_captures
    key = f"{BACKUP_PREFIX}wastelens-{now:%Y%m%dT%H%M%SZ}.sql.gz"

    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump.sql"
        subprocess.run(
            [
                "pg_dump",
                "--no-owner",
                "--no-privileges",
                "-f",
                str(dump_path),
                _pg_dump_dsn(settings.database_url),
            ],
            check=True,
            timeout=600,
        )
        body = gzip.compress(dump_path.read_bytes())

    storage.client().put_object(
        Bucket=bucket, Key=key, Body=body, ContentType="application/gzip"
    )
    log.info("database_backed_up", key=key, bucket=bucket, size_bytes=len(body))

    _prune_old_backups(bucket, now, settings.backup_retention_days)
    return key


def _prune_old_backups(bucket: str, now: dt.datetime, retention_days: int) -> None:
    client = storage.client()
    cutoff = now - dt.timedelta(days=retention_days)
    paginator = client.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=BACKUP_PREFIX):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                client.delete_object(Bucket=bucket, Key=obj["Key"])
                deleted += 1
    if deleted:
        log.info("old_backups_pruned", count=deleted, retention_days=retention_days)
