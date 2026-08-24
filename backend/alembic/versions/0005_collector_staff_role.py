"""Phase 4: add 'collector' to the staff_role enum, for field staff running
a collection route (Collector, operations.py, is a 1-1 profile keyed off
this role, added in Phase 1 but never reachable via login until now).

ALTER TYPE ... ADD VALUE is safe inside Alembic's normal transactional
migration wrapper on Postgres 12+ as long as nothing in the same migration
*uses* the new value — this migration only adds it, so no autocommit block
is needed.

Downgrade does not remove the enum value: Postgres has no DROP VALUE, so
reverting would mean rebuilding staff_role from scratch and rewriting every
column that uses it. Left as a documented no-op — an unused extra enum
value on downgrade is harmless.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE staff_role ADD VALUE IF NOT EXISTS 'collector'")


def downgrade() -> None:
    # See module docstring — removing an enum value isn't supported here.
    pass
