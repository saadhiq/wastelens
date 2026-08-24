"""Phase 7: Capture.image_purged_at — tracks whether the retention job has
deleted this capture's S3 image yet. Nullable, additive: every existing
capture's image still exists.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "captures", sa.Column("image_purged_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("captures", "image_purged_at")
