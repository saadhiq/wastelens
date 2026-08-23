"""Facility operations: pickup scheduling, field staff, inspection stations,
and downstream bins/vendors. Separate from waste.py's capture-and-detect
core flow — this is the logistics side (who collects what, when, and where
sorted material goes after processing). New in Phase 1's domain extension.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BinType, LightingCondition, PickupChannel, PickupStatus


class PickupRequest(Base):
    """A resident's request for a collection — the demand side. A
    CollectionSession (waste.py) is the supply-side fulfillment of one."""

    __tablename__ = "pickup_requests"
    __table_args__ = (Index("ix_pickup_requests_date_status", "requested_for_date", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    requested_for_date: Mapped[dt.date] = mapped_column()
    requested_window: Mapped[str | None] = mapped_column(String(50), nullable=True)
    requested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    channel: Mapped[PickupChannel] = mapped_column(Enum(PickupChannel, name="pickup_channel"))
    declared_bag_count: Mapped[int | None] = mapped_column(nullable=True)
    # No standalone index on status: it's covered by the composite index
    # below (requested_for_date, status), which is the query this table
    # actually needs to serve.
    status: Mapped[PickupStatus] = mapped_column(
        Enum(PickupStatus, name="pickup_status"), default=PickupStatus.REQUESTED
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when a CollectionSession actually fulfills this request.
    collection_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collection_sessions.id", ondelete="SET NULL"), nullable=True
    )


class Collector(Base):
    """A field-staff member who drives a collection route. 1—1 with a
    StaffAccount (the login) — kept separate because not every StaffAccount
    is a collector, and a collector needs fields (employee_code, vehicle)
    that a station_operator/reviewer/analyst account never does."""

    __tablename__ = "collectors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staff_accounts.id", ondelete="CASCADE"), unique=True, index=True
    )
    employee_code: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    default_vehicle_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class InspectionStation(Base):
    """A catalogued capture station on the sorting line. Distinct from the
    free-text `station_id` string already on Capture (kept as-is, see
    waste.py) — this is the richer record a capture can optionally point at
    via `inspection_station_id`."""

    __tablename__ = "inspection_stations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_code: Mapped[str] = mapped_column(String(50), unique=True)
    facility_name: Mapped[str] = mapped_column(String(200))
    line_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    camera_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_lighting: Mapped[LightingCondition | None] = mapped_column(
        Enum(LightingCondition, name="lighting_condition", create_type=False), nullable=True
    )


class Bin(Base):
    """A downstream container that sorted bags get transferred into en route
    to processing/disposal/a vendor."""

    __tablename__ = "bins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bin_code: Mapped[str] = mapped_column(String(50), unique=True)
    bin_type: Mapped[BinType] = mapped_column(Enum(BinType, name="bin_type"))
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    capacity_kg: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    downstream_process: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)


class BinTransfer(Base):
    """One bag's physical hand-off from a tray into a bin — the paper trail
    connecting a resident's bag to whichever downstream vendor/process it
    actually went to. `bin_id` is RESTRICT (not CASCADE/SET NULL): a Bin with
    transfer history can't be deleted out from under that audit trail."""

    __tablename__ = "bin_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bags.id", ondelete="CASCADE"), index=True)
    from_tray_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bin_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bins.id", ondelete="RESTRICT"), index=True
    )
    transferred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True
    )
    weight_kg: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
