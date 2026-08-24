"""Living taxonomies: the item vocabulary the CV prompts are built from, and the
brand list that OCR text is fuzzy-matched against. Both are editable at runtime
(admin CRUD in Phase 2) — the pipeline always loads them from the DB, never
from code.
"""

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BagType, Base, UnmappedLabelKind


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

    # --- Phase 1 domain extension: category tree + sensitivity flags ---
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item_vocabulary.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_contaminant_by_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Medication, pregnancy/infant products, sanitary products, contraceptives,
    # alcohol, tobacco, religious-dietary items, personal correspondence.
    # Operational handling still sees these; profiling never does.
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UnmappedLabel(Base):
    """A raw label the vision model produced that didn't match its target
    catalog. Two kinds share this table (see UnmappedLabelKind): an ITEM row
    is an item_name that didn't match the active vocabulary — the
    demoted-to-`unidentified_item` case in services/analysis.py; a BRAND row
    (Phase 5) is packaging brand_text that didn't fuzzy-match any Brand
    above threshold. Tracked here (rather than only in Detection's free
    text) so a recurring unmapped label surfaces as a concrete candidate for
    a new VocabularyItem/Brand instead of getting lost.

    The ITEM side is still not written to by the pipeline — only the BRAND
    side is wired up, in Phase 5's brand-matching work.
    """

    __tablename__ = "unmapped_labels"
    # One row per distinct (raw_label, bag_type, label_kind); occurrence_count
    # increments on repeats rather than growing a new row per sighting.
    # label_kind is part of the key (not just bag_type) so an item name and a
    # brand that happen to share text (e.g. "Sunlight") never collide.
    __table_args__ = (
        UniqueConstraint(
            "raw_label", "bag_type", "label_kind", name="uq_unmapped_label_bagtype_kind"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_label: Mapped[str] = mapped_column(String(200))
    bag_type: Mapped[BagType] = mapped_column(
        Enum(BagType, name="bag_type", create_type=False), index=True
    )
    label_kind: Mapped[UnmappedLabelKind] = mapped_column(
        Enum(UnmappedLabelKind, name="unmapped_label_kind"),
        default=UnmappedLabelKind.ITEM,
        server_default=UnmappedLabelKind.ITEM.value,
        index=True,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    suggested_vocabulary_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item_vocabulary.id", ondelete="SET NULL"), nullable=True
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
