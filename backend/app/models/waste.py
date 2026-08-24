"""The waste-flow entities: bags, collection sessions, captures, detections.

Flow: a Resident's tagged Bag is collected in a CollectionSession → at the
facility, each bag is emptied onto a tray and photographed (Capture) → the CV
pipeline writes one Detection row per item found on the tray.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AnalysisStatus,
    BagCondition,
    BagStatus,
    BagType,
    Base,
    InferenceRunStatus,
    ItemState,
    LightingCondition,
    ReviewStatus,
    ReviewVerdict,
)


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

    # --- Phase 1 domain extension: physical weight + condition at handoff ---
    gross_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    tare_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    bag_condition: Mapped[BagCondition | None] = mapped_column(
        Enum(BagCondition, name="bag_condition"), nullable=True
    )
    assigned_bin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bins.id", ondelete="SET NULL"), nullable=True
    )

    # --- Phase 4: which collection run picked this bag up ---
    # Phase 1 only linked a Bag to a CollectionSession indirectly, through
    # Capture (session_id + bag_id) — but a Capture only exists once the bag
    # reaches the station. The collector needs to attach bags to a session
    # at the doorstep, before any capture happens, so that link has to live
    # on Bag directly. Nullable: a bag can be registered long before it's
    # ever collected.
    collection_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collection_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    session: Mapped["CollectionSession | None"] = relationship(back_populates="bags")

    @property
    def net_weight_kg(self) -> Decimal | None:
        """Computed, not stored — None until both weights are recorded."""
        if self.gross_weight_kg is None or self.tare_weight_kg is None:
            return None
        return self.gross_weight_kg - self.tare_weight_kg


class CollectionSession(Base):
    """One household collection event; groups the (up to 4) bag captures."""

    __tablename__ = "collection_sessions"
    __table_args__ = (Index("ix_collection_sessions_user_collected", "user_id", "collected_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    collected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # --- Phase 1 domain extension: who collected it, on what route, from where ---
    collector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collectors.id", ondelete="SET NULL"), nullable=True
    )
    vehicle_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    route_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gps_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    gps_longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    warehouse_arrival_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    captures: Mapped[list["Capture"]] = relationship(back_populates="session")
    bags: Mapped[list["Bag"]] = relationship(back_populates="session")


class Capture(Base):
    """One tray photo of one emptied bag at a capture station.

    `station_id` (free-text, set by whatever the uploading station sends)
    predates the richer `InspectionStation` catalog added in Phase 1 — kept
    as-is per the "keep all existing columns" rule. The new
    `inspection_station_id` FK is a separate, optional way to point at a
    catalogued station without disturbing that existing column or its
    callers. See DECISIONS.md.
    """

    __tablename__ = "captures"
    __table_args__ = (
        UniqueConstraint("bag_id", "image_sha256", name="uq_captures_bag_image_sha256"),
        Index("ix_captures_bag_captured", "bag_id", "captured_at"),
    )

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

    # --- Phase 1 domain extension: image provenance + capture conditions ---
    inspection_station_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inspection_stations.id", ondelete="SET NULL"), nullable=True
    )
    tray_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Paired with the (bag_id, image_sha256) unique constraint above to block
    # duplicate uploads of the same photo for the same bag.
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lighting_condition: Mapped[LightingCondition | None] = mapped_column(
        Enum(LightingCondition, name="lighting_condition"), nullable=True
    )

    session: Mapped[CollectionSession] = relationship(back_populates="captures")
    detections: Mapped[list["Detection"]] = relationship(back_populates="capture")
    inference_runs: Mapped[list["InferenceRun"]] = relationship(back_populates="capture")


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

    # --- Phase 1 domain extension: raw OCR fragments, item condition, geometry ---
    # brand_text is the raw string the model read; matched_brand_id above is
    # the fuzzy-matched result. We keep BOTH — an unmatched brand_text is the
    # most commercially valuable signal in the system: a product we don't
    # know about yet. See DECISIONS.md.
    brand_text: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    product_name_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_size_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    barcode_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_state: Mapped[ItemState | None] = mapped_column(
        Enum(ItemState, name="item_state"), nullable=True, index=True
    )
    is_contaminant: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    estimated_weight_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    count_est: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nullable for now — every detection to date predates InferenceRun;
    # backfilled in Phase 2 once the pipeline starts writing it.
    inference_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inference_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    capture: Mapped[Capture] = relationship(back_populates="detections")
    human_reviews: Mapped[list["HumanReview"]] = relationship(back_populates="detection")


class InferenceRun(Base):
    """One vision-model call attempt for a Capture. The existing pipeline's
    single repair retry (DECISIONS.md #10a) becomes attempt_no=2 here — both
    attempts are kept, including failed ones, since a failed attempt is
    training/prompt-tuning signal, not noise to discard. See DECISIONS.md
    for why this is its own table rather than columns on Capture.

    Detection.inference_run_id (nullable for now, backfilled in Phase 2) will
    eventually point every detection back to the specific attempt that
    produced it.
    """

    __tablename__ = "inference_runs"
    __table_args__ = (
        UniqueConstraint("capture_id", "attempt_no", name="uq_inference_run_capture_attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    capture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("captures.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str] = mapped_column(String(200), index=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[InferenceRunStatus] = mapped_column(
        Enum(InferenceRunStatus, name="inference_run_status"), index=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_predicted_bag_type: Mapped[BagType | None] = mapped_column(
        Enum(BagType, name="bag_type", create_type=False), nullable=True
    )
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # For output that failed to parse at all — raw_response stays empty and
    # the unparseable text lands here instead, same spirit as
    # Detection.raw_model_output today.
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    capture: Mapped[Capture] = relationship(back_populates="inference_runs")


class HumanReview(Base):
    """One reviewer's decision on one Detection (Phase 3). Append-only audit
    trail of review actions — a Detection can in principle be reviewed more
    than once (e.g. a QA second pass), so this is 1—N from Detection, not
    1—1; `Detection.review_status`/`corrected_item_name` always reflect the
    *latest* HumanReview, kept in sync by services/review.py.apply_review().

    The model's original output on Detection is never touched here either —
    corrections live only in this table's corrected_* columns, same
    principle as Detection's own docstring.
    """

    __tablename__ = "human_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    detection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detections.id", ondelete="CASCADE"), index=True
    )
    # Every review has a definite reviewer at creation time (unlike
    # Capture.operator_id, which is genuinely optional) — RESTRICT, not
    # SET NULL, so a staff account with review history can't be deleted
    # out from under this audit trail. In practice accounts are
    # deactivated (is_active=False), not deleted, so this rarely bites.
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staff_accounts.id", ondelete="RESTRICT"), index=True
    )
    reviewed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    verdict: Mapped[ReviewVerdict] = mapped_column(
        Enum(ReviewVerdict, name="review_verdict"), index=True
    )
    corrected_item_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    corrected_brand_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corrected_is_contaminant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    detection: Mapped[Detection] = relationship(back_populates="human_reviews")
