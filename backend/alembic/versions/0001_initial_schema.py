"""Initial WasteLens schema: residents, staff, bags, sessions, captures,
detections, vocabulary, brands, profiles, audit log.

Revision ID: 0001
Revises:
Create Date: 2026-07-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

bag_type = postgresql.ENUM(
    "organic", "polythene", "paper", "general", name="bag_type", create_type=False
)
bag_status = postgresql.ENUM(
    "registered", "collected", "processed", name="bag_status", create_type=False
)
analysis_status = postgresql.ENUM(
    "pending", "processing", "done", "failed", name="analysis_status", create_type=False
)
review_status = postgresql.ENUM(
    "unreviewed", "confirmed", "corrected", "rejected", name="review_status", create_type=False
)
staff_role = postgresql.ENUM(
    "admin", "station_operator", "reviewer", "analyst", name="staff_role", create_type=False
)

_ENUMS = (bag_type, bag_status, analysis_status, review_status, staff_role)


def upgrade() -> None:
    bind = op.get_bind()
    for e in _ENUMS:
        e.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)

    op.create_table(
        "staff_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("hashed_password", sa.String(200), nullable=False),
        sa.Column("role", staff_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_staff_accounts_email", "staff_accounts", ["email"], unique=True)

    op.create_table(
        "bags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bag_type", bag_type, nullable=False),
        sa.Column("tag_id", sa.String(64), nullable=False),
        sa.Column("status", bag_status, nullable=False, server_default="registered"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_bags_user_id", "bags", ["user_id"])
    op.create_index("ix_bags_tag_id", "bags", ["tag_id"], unique=True)

    op.create_table(
        "collection_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_collection_sessions_user_id", "collection_sessions", ["user_id"])

    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "captures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collection_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bag_type", bag_type, nullable=False),
        sa.Column("image_url", sa.String(1000), nullable=False),
        sa.Column("station_id", sa.String(64), nullable=False),
        sa.Column(
            "operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("staff_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("analysis_status", analysis_status, nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(128), nullable=True, unique=True),
    )
    op.create_index("ix_captures_session_id", "captures", ["session_id"])
    op.create_index("ix_captures_bag_id", "captures", ["bag_id"])
    op.create_index("ix_captures_analysis_status", "captures", ["analysis_status"])

    op.create_table(
        "detections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "capture_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("captures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_name", sa.String(100), nullable=False),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("estimated_quantity", sa.Text(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column(
            "matched_brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_status", review_status, nullable=False, server_default="unreviewed"),
        sa.Column("corrected_item_name", sa.String(100), nullable=True),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("staff_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_model_output", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_detections_capture_id", "detections", ["capture_id"])
    op.create_index("ix_detections_item_name", "detections", ["item_name"])
    op.create_index(
        "ix_detections_needs_review_status", "detections", ["needs_review", "review_status"]
    )

    op.create_table(
        "item_vocabulary",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bag_type", bag_type, nullable=False),
        sa.Column("item_name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("parent_category", sa.String(100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("bag_type", "item_name", name="uq_vocab_bagtype_item"),
    )
    op.create_index("ix_item_vocabulary_bag_type", "item_vocabulary", ["bag_type"])

    op.create_table(
        "user_waste_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("veg_frequency", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_vegetables", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("packaged_food_frequency", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_brands", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("category_breakdown", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "rebuilt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "week_start", name="uq_profile_user_week"),
    )
    op.create_index("ix_user_waste_profiles_user_id", "user_waste_profiles", ["user_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("staff_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    for table in (
        "audit_log",
        "user_waste_profiles",
        "item_vocabulary",
        "detections",
        "captures",
        "brands",
        "collection_sessions",
        "bags",
        "staff_accounts",
        "users",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for e in _ENUMS:
        e.drop(bind, checkfirst=True)
