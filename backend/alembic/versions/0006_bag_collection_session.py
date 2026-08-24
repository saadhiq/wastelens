"""Phase 4: Bag.collection_session_id — links a bag to the collection run
that picked it up. Previously the only link from a Bag to a
CollectionSession was indirect, through Capture (session_id + bag_id),
which doesn't exist until the bag reaches the station. The collector's
doorstep flow (POST /sessions with nested bags) needs this link the moment
bags are added, well before any capture happens.

Nullable: a registered bag can sit uncollected indefinitely, and every
existing bag predates this column.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bags",
        sa.Column(
            "collection_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collection_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_bags_collection_session_id", "bags", ["collection_session_id"])


def downgrade() -> None:
    op.drop_index("ix_bags_collection_session_id", table_name="bags")
    op.drop_column("bags", "collection_session_id")
