"""Phase 5: Detection.material_type — packaging composition (e.g. "PET
plastic", "cardboard") read off paper/polythene items by the new v2
extraction prompt. Nullable: every existing detection predates it, and
organic/general detections never populate it.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("material_type", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("detections", "material_type")
