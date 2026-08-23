"""The waste-flow entities: bags, collection sessions, captures, detections.

Flow: a Resident's tagged Bag is collected in a CollectionSession → at the
facility, each bag is emptied onto a tray and photographed (Capture) → the CV
pipeline writes one Detection row per item found on the tray.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AnalysisStatus, BagStatus, BagType, Base, ReviewStatus


class Bag(Base):
    __tablename__ = "bags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    bag_type: Mapped[BagType] = mapped_column(Enum(BagType, name="bag_type"))
    tag_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # QR code payload
    status: Mapped[BagStatus] = mapped_column(
        Enum(BagStatus, name="bag_status"), default=BagStatus.registered
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CollectionSession(Base):
    """One household collection event; groups the (up to 4) bag captures."""

    __tablename__ = "collection_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    collected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    captures: Mapped[list["Capture"]] = relationship(back_populates="session")


class Capture(Base):
    """One tray photo of one emptied bag at a capture station."""

    __tablename__ = "captures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collection_sessions.id", ondelete="CASCADE"), index=True
    )
    bag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bags.id", ondelete="CASCADE"), index=True)
    bag_type: Mapped[BagType] = mapped_column(Enum(BagType, name="bag_type", create_type=False))
    image_url: Mapped[str] = mapped_column(String(1000))
    station_id: Mapped[str] = mapped_column(String(64))
    operator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True
    )
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus, name="analysis_status"), default=AnalysisStatus.pending, index=True
    )
    # Idempotency key supplied by the station so flaky uploads can retry safely.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)

    session: Mapped[CollectionSession] = relationship(back_populates="captures")
    detections: Mapped[list["Detection"]] = relationship(back_populates="capture")


class Detection(Base):
    """One detected waste item on a tray. The model's original guess is never
    overwritten — corrections land in corrected_item_name so review data can
    serve as future training data."""

    __tablename__ = "detections"
    __table_args__ = (Index("ix_detections_needs_review_status", "needs_review", "review_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    capture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), index=True
    )
    item_name: Mapped[str] = mapped_column(String(100), index=True)  # snake_case vocab name
    subcategory: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    estimated_quantity: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.unreviewed
    )
    corrected_item_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_model_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    capture: Mapped[Capture] = relationship(back_populates="detections")
