"""Image retention (Phase 7): delete tray images older than the configured
window from object storage, keeping every derived row (Capture, Detection,
InferenceRun, ...) untouched — only the S3 object and Capture.image_purged_at
change. See DECISIONS.md for the chosen window and why the model, review,
and analytics data all stay intact indefinitely.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models import Capture
from app.services import storage

log = get_logger(__name__)


def purge_old_images(db: Session, *, now: dt.datetime | None = None) -> int:
    """Deletes the S3 object for every capture older than
    image_retention_days that hasn't already been purged. Returns the
    number of images deleted. Idempotent: a capture with image_purged_at
    already set is skipped, so re-running never re-attempts a delete."""
    settings = get_settings()
    now = now or dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=settings.image_retention_days)

    captures = db.scalars(
        select(Capture).where(Capture.captured_at < cutoff, Capture.image_purged_at.is_(None))
    ).all()

    purged = 0
    for capture in captures:
        storage.delete_image(capture.image_url)
        capture.image_purged_at = now
        purged += 1

    db.commit()
    log.info("images_purged", count=purged, retention_days=settings.image_retention_days)
    return purged
