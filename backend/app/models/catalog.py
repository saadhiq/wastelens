"""Living taxonomies: the item vocabulary the CV prompts are built from, and the
brand list that OCR text is fuzzy-matched against. Both are editable at runtime
(admin CRUD in Phase 2) — the pipeline always loads them from the DB, never
from code.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BagType, Base


class VocabularyItem(Base):
    __tablename__ = "item_vocabulary"
    __table_args__ = (UniqueConstraint("bag_type", "item_name", name="uq_vocab_bagtype_item"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bag_type: Mapped[BagType] = mapped_column(
        Enum(BagType, name="bag_type", create_type=False), index=True
    )
    item_name: Mapped[str] = mapped_column(String(100))  # snake_case
    display_name: Mapped[str] = mapped_column(String(200))
    parent_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
