"""Phase 5: UnmappedLabel now serves two queues — item-vocabulary candidates
(the original Phase 1 use, still unwired) and packaging brand_text that
didn't fuzzy-match any Brand (new, from paper/polythene extraction). Adds
the label_kind discriminator (default ITEM, so every existing/future
unqualified row keeps today's meaning) and widens the uniqueness key so an
item and a brand sharing the same raw text in the same bag_type don't
collide.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

unmapped_label_kind = postgresql.ENUM(
    "ITEM", "BRAND", name="unmapped_label_kind", create_type=False
)
_ENUMS = (unmapped_label_kind,)


def upgrade() -> None:
    bind = op.get_bind()
    for e in _ENUMS:
        e.create(bind, checkfirst=True)

    op.add_column(
        "unmapped_labels",
        sa.Column(
            "label_kind",
            unmapped_label_kind,
            nullable=False,
            server_default="ITEM",
        ),
    )
    op.create_index("ix_unmapped_labels_label_kind", "unmapped_labels", ["label_kind"])

    op.drop_constraint("uq_unmapped_label_bagtype", "unmapped_labels", type_="unique")
    op.create_unique_constraint(
        "uq_unmapped_label_bagtype_kind", "unmapped_labels", ["raw_label", "bag_type", "label_kind"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_unmapped_label_bagtype_kind", "unmapped_labels", type_="unique")
    op.create_unique_constraint(
        "uq_unmapped_label_bagtype", "unmapped_labels", ["raw_label", "bag_type"]
    )

    op.drop_index("ix_unmapped_labels_label_kind", table_name="unmapped_labels")
    op.drop_column("unmapped_labels", "label_kind")

    bind = op.get_bind()
    for e in _ENUMS:
        e.drop(bind, checkfirst=True)
