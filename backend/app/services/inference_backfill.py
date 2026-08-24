"""One-time backfill: give every pre-Phase-2 Capture-with-Detections a
synthetic InferenceRun so no Detection is left without one (Phase 2 point 4).

Kept as a plain, ORM-based function — not embedded raw SQL inside the
migration — specifically so it's directly unit-testable against the ORM the
same way the rest of this project's pipeline tests are (test_pipeline.py's
style). This is a deliberate, narrow exception to decision #5's "migrations
don't depend on app code" principle: it's a one-shot *data* backfill (not a
schema change) invoked by migration 0003, where testability was an explicit
deliverable.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Detection, InferenceRun, InferenceRunStatus

BACKFILL_NOTE = "backfilled — original run metadata not captured"


def _configured_model_name(settings: Settings) -> str:
    return (
        settings.nvidia_vision_model
        if settings.vision_provider.lower() == "nvidia"
        else settings.vision_model
    )


def backfill_missing_inference_runs(db: Session) -> int:
    """For every Capture that has Detections but no InferenceRun yet, create
    one synthetic attempt_no=1 SUCCESS run and link those Detections to it.
    Returns the number of InferenceRuns created. Safe to run more than
    once — captures that already have a run are skipped."""
    settings = get_settings()
    provider_name = settings.vision_provider
    model_name = _configured_model_name(settings)

    has_detections = set(db.scalars(select(Detection.capture_id).distinct()))
    has_runs = set(db.scalars(select(InferenceRun.capture_id).distinct()))
    missing = has_detections - has_runs

    for capture_id in missing:
        run = InferenceRun(
            capture_id=capture_id,
            attempt_no=1,
            provider_name=provider_name,
            model_name=model_name,
            status=InferenceRunStatus.SUCCESS,
            raw_response=None,
            error_message=BACKFILL_NOTE,
        )
        db.add(run)
        db.flush()
        db.query(Detection).filter(
            Detection.capture_id == capture_id, Detection.inference_run_id.is_(None)
        ).update({"inference_run_id": run.id})

    db.commit()
    return len(missing)


def undo_backfill_missing_inference_runs(db: Session) -> int:
    """Reverse backfill_missing_inference_runs: unlink and delete only the
    InferenceRuns it created (identified by BACKFILL_NOTE), leaving any
    real InferenceRuns untouched. Returns the number removed."""
    backfilled = db.scalars(
        select(InferenceRun).where(InferenceRun.error_message == BACKFILL_NOTE)
    ).all()
    ids = [run.id for run in backfilled]
    if ids:
        db.query(Detection).filter(Detection.inference_run_id.in_(ids)).update(
            {"inference_run_id": None}, synchronize_session=False
        )
        db.query(InferenceRun).filter(InferenceRun.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    return len(ids)
