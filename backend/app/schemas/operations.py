"""Schemas for Phase 4's operations surface: pickup bookings, field-staff
collectors, inspection stations, bins/transfers, and the reference calendar."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel

from app.models.base import BagCondition, BinType, LightingCondition, PickupChannel, PickupStatus

# --- Pickups -----------------------------------------------------------


class PickupRequestCreate(BaseModel):
    user_id: uuid.UUID
    requested_for_date: dt.date
    requested_window: str | None = None
    channel: PickupChannel
    declared_bag_count: int | None = None


class PickupRequestOut(BaseModel):
    id: uuid.UUID
    resident_id: uuid.UUID
    requested_for_date: dt.date
    requested_window: str | None
    requested_at: dt.datetime
    channel: PickupChannel
    declared_bag_count: int | None
    status: PickupStatus
    cancel_reason: str | None
    collection_session_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class PickupCancel(BaseModel):
    reason: str | None = None


# --- Collectors ----------------------------------------------------------


class CollectorCreate(BaseModel):
    staff_account_id: uuid.UUID
    employee_code: str
    full_name: str
    phone: str | None = None
    default_vehicle_code: str | None = None


class CollectorUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    default_vehicle_code: str | None = None
    is_active: bool | None = None


class CollectorOut(BaseModel):
    id: uuid.UUID
    staff_account_id: uuid.UUID
    employee_code: str
    full_name: str
    phone: str | None
    default_vehicle_code: str | None
    is_active: bool

    model_config = {"from_attributes": True}


# --- Inspection stations --------------------------------------------------


class StationCreate(BaseModel):
    station_code: str
    facility_name: str
    line_name: str | None = None
    camera_identifier: str | None = None
    default_lighting: LightingCondition | None = None


class StationUpdate(BaseModel):
    facility_name: str | None = None
    line_name: str | None = None
    camera_identifier: str | None = None
    default_lighting: LightingCondition | None = None


class StationOut(BaseModel):
    id: uuid.UUID
    station_code: str
    facility_name: str
    line_name: str | None
    camera_identifier: str | None
    default_lighting: LightingCondition | None

    model_config = {"from_attributes": True}


# --- Bins ------------------------------------------------------------------


class BinCreate(BaseModel):
    bin_code: str
    bin_type: BinType
    location: str | None = None
    capacity_kg: Decimal | None = None
    downstream_process: str | None = None
    vendor_name: str | None = None


class BinUpdate(BaseModel):
    location: str | None = None
    capacity_kg: Decimal | None = None
    downstream_process: str | None = None
    vendor_name: str | None = None


class BinOut(BaseModel):
    id: uuid.UUID
    bin_code: str
    bin_type: BinType
    location: str | None
    capacity_kg: Decimal | None
    downstream_process: str | None
    vendor_name: str | None

    model_config = {"from_attributes": True}


class BinTransferCreate(BaseModel):
    bag_id: uuid.UUID
    bin_id: uuid.UUID
    from_tray_code: str | None = None
    weight_kg: Decimal | None = None


class BinTransferOut(BaseModel):
    id: uuid.UUID
    bag_id: uuid.UUID
    from_tray_code: str | None
    bin_id: uuid.UUID
    transferred_at: dt.datetime
    operator_id: uuid.UUID | None
    weight_kg: Decimal | None

    model_config = {"from_attributes": True}


# --- Calendar ----------------------------------------------------------


class CalendarDayOut(BaseModel):
    calendar_date: dt.date
    day_of_week: int
    is_weekend: bool
    is_poya: bool
    is_public_holiday: bool
    note: str | None

    model_config = {"from_attributes": True}


class CalendarDayUpdate(BaseModel):
    """Admin-editable fields only — day_of_week/is_weekend are derived from
    the date itself and never change after seeding."""

    is_poya: bool | None = None
    is_public_holiday: bool | None = None
    note: str | None = None


class BagWeighIn(BaseModel):
    gross_weight_kg: Decimal | None = None
    tare_weight_kg: Decimal | None = None
    bag_condition: BagCondition | None = None
