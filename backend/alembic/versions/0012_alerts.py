"""Phase 7: alerts table — failed-vision-run-rate and daily-spend breaches
(services/alerting.py). No external notification channel exists in this
project; this table is the alert surface itself. Brand new table, no
backfill.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

alert_type = postgresql.ENUM("FAILED_RUN_RATE", "DAILY_SPEND", name="alert_type", create_type=False)
_ENUMS = (alert_type,)


def upgrade() -> None:
    bind = op.get_bind()
    for e in _ENUMS:
        e.create(bind, checkfirst=True)

    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_type", alert_type, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metric_value", sa.Numeric(12, 4), nullable=False),
        sa.Column("threshold", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_alerts_alert_type", "alerts", ["alert_type"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_index("ix_alerts_alert_type", table_name="alerts")
    op.drop_table("alerts")

    bind = op.get_bind()
    for e in _ENUMS:
        e.drop(bind, checkfirst=True)
