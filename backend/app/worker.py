"""Celery application. Phase 0 ships the app + a ping task so the worker
container comes up healthy; CV analysis jobs land here in Phase 1."""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "wastelens",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # Nightly profile rebuild (worker runs with embedded beat: `-B`).
    beat_schedule={
        "rebuild-waste-profiles-nightly": {
            "task": "app.worker.rebuild_profiles",
            "schedule": 60 * 60 * 24,  # every 24h
        },
        # Phase 6: independent of the rebuild above — both re-derive
        # straight from Detection/Capture, neither reads the other's
        # output, so there's no ordering dependency between them.
        "compute-consumption-signals-nightly": {
            "task": "app.worker.compute_consumption_signals",
            "schedule": 60 * 60 * 24,  # every 24h
        },
        # Phase 7: image retention.
        "purge-old-images-nightly": {
            "task": "app.worker.purge_old_images",
            "schedule": 60 * 60 * 24,  # every 24h
        },
        # Phase 7: alerting (failed-run rate, daily spend) — more frequent
        # than the nightly jobs above, since an alert is only useful if it
        # fires close to the problem it's flagging.
        "check-alerts-hourly": {
            "task": "app.worker.check_alerts",
            "schedule": 60 * 60,  # every 1h
        },
        # Phase 8: full-database backup to S3.
        "backup-database-nightly": {
            "task": "app.worker.backup_database",
            "schedule": 60 * 60 * 24,  # every 24h
        },
    },
)


@celery_app.task(name="app.worker.ping")
def ping() -> str:
    """Smoke-test task: `celery -A app.worker.celery_app call app.worker.ping`."""
    return "pong"


@celery_app.task(
    name="app.worker.analyze_capture",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def analyze_capture_task(self, capture_id: str) -> None:
    """Run the CV pipeline for one capture. Retries transient failures
    (network, provider 5xx); permanent failures mark the capture `failed`."""
    import uuid

    from app.db import SessionLocal
    from app.services.analysis import analyze_capture

    db = SessionLocal()
    try:
        analyze_capture(db, uuid.UUID(capture_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    finally:
        db.close()


@celery_app.task(name="app.worker.rebuild_profiles")
def rebuild_profiles_task(weeks_back: int = 2) -> int:
    """Rebuild weekly waste profiles (scheduled nightly; also triggered via
    POST /api/v1/profiles/rebuild)."""
    from app.db import SessionLocal
    from app.services.aggregation import rebuild_recent

    db = SessionLocal()
    try:
        return rebuild_recent(db, weeks_back=weeks_back)
    finally:
        db.close()


@celery_app.task(name="app.worker.compute_consumption_signals")
def compute_consumption_signals_task() -> int:
    """Rebuild household consumption signals — replenishment cycles and
    predicted next-disposal dates (Phase 6; scheduled nightly)."""
    from app.db import SessionLocal
    from app.services.profiling import compute_consumption_signals

    db = SessionLocal()
    try:
        return compute_consumption_signals(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.purge_old_images")
def purge_old_images_task() -> int:
    """Delete tray images past the retention window, keeping every derived
    row (Phase 7; scheduled nightly)."""
    from app.db import SessionLocal
    from app.services.retention import purge_old_images

    db = SessionLocal()
    try:
        return purge_old_images(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.check_alerts")
def check_alerts_task() -> int:
    """Check the failed-run rate and daily spend against their configured
    thresholds, writing an Alert row for each breach (Phase 7; scheduled
    hourly). Returns the number of new alerts written."""
    from app.db import SessionLocal
    from app.services.alerting import check_alerts

    db = SessionLocal()
    try:
        return len(check_alerts(db))
    finally:
        db.close()


@celery_app.task(name="app.worker.backup_database")
def backup_database_task() -> str:
    """Dump the full database and upload it to S3 (Phase 8; scheduled
    nightly; also triggered via POST /api/v1/admin/backups/run). Returns
    the S3 key written."""
    from app.services.backup import run_backup

    return run_backup()
