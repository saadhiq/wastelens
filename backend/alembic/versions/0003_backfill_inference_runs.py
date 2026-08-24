"""Backfill InferenceRun rows for existing Captures that already have
Detections but predate InferenceRun (Phase 2 point 4).

For every such Capture, creates one synthetic attempt_no=1 SUCCESS
InferenceRun (provider/model read from settings at backfill time,
raw_response null, error_message noting it's a backfill) and links that
capture's Detections to it. No pre-existing Detection is left with a null
inference_run_id after this runs.

The actual logic lives in app.services.inference_backfill, not inline here
— a deliberate, narrow exception to decision #5's "migrations don't depend
on app code": this is a one-shot *data* backfill, not a schema change, and
having it as a plain ORM function makes it directly unit-testable (see
tests/test_pipeline.py), which a fully self-contained SQL migration would
not be.

Reversible: downgrade removes only the InferenceRuns this migration itself
created (identified by their error_message marker), not any real ones.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from alembic import op
from app.services.inference_backfill import (
    backfill_missing_inference_runs,
    undo_backfill_missing_inference_runs,
)

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    session = Session(bind=op.get_bind())
    created = backfill_missing_inference_runs(session)
    print(f"0003: backfilled {created} InferenceRun row(s)")


def downgrade() -> None:
    session = Session(bind=op.get_bind())
    removed = undo_backfill_missing_inference_runs(session)
    print(f"0003 downgrade: removed {removed} backfilled InferenceRun row(s)")
