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
