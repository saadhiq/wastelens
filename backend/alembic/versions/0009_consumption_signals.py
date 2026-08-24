"""Phase 6: consumption_signals table — one row per (resident, category or
brand), holding the replenishment-cycle stats and predicted next-disposal
date the nightly job (services/profiling.py) computes. Brand new table, no
backfill: nothing computed this data before Phase 6 existed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

consumption_signal_subject_type = postgresql.ENUM(
    "CATEGORY", "BRAND", name="consumption_signal_subject_type", create_type=False
)
_ENUMS = (consumption_signal_subject_type,)


def upgrade() -> None:
    bind = op.get_bind()
    for e in _ENUMS:
        e.create(bind, checkfirst=True)

    op.create_table(
        "consumption_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", consumption_signal_subject_type, nullable=False),
        sa.Column("subject_value", sa.String(200), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("disposal_dates", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("replenishment_cycle_days_mean", sa.Numeric(8, 2), nullable=True),
        sa.Column("replenishment_cycle_days_stddev", sa.Numeric(8, 2), nullable=True),
        sa.Column("last_disposal_date", sa.Date(), nullable=True),
        sa.Column("predicted_next_disposal_date", sa.Date(), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.UniqueConstraint(
            "resident_id", "subject_type", "subject_value", name="uq_consumption_signal_subject"
        ),
    )
    op.create_index("ix_consumption_signals_resident_id", "consumption_signals", ["resident_id"])
    op.create_index("ix_consumption_signals_subject_type", "consumption_signals", ["subject_type"])
    op.create_index("ix_consumption_signals_category", "consumption_signals", ["category"])


def downgrade() -> None:
    op.drop_index("ix_consumption_signals_category", table_name="consumption_signals")
    op.drop_index("ix_consumption_signals_subject_type", table_name="consumption_signals")
    op.drop_index("ix_consumption_signals_resident_id", table_name="consumption_signals")
    op.drop_table("consumption_signals")

    bind = op.get_bind()
    for e in _ENUMS:
        e.drop(bind, checkfirst=True)
