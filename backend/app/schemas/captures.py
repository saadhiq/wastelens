"""Schemas for bags, collection sessions, captures, and detections."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.base import (
    AnalysisStatus,
    BagCondition,
    BagStatus,
    BagType,
    InferenceRunStatus,
    ItemState,
    LightingCondition,
    ReviewStatus,
)


class BagRegister(BaseModel):
    user_id: uuid.UUID
    bag_type: BagType
    tag_id: str


class BagOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    bag_type: BagType
    tag_id: str
    status: BagStatus
    gross_weight_kg: Decimal | None
    tare_weight_kg: Decimal | None
    net_weight_kg: Decimal | None
    bag_condition: BagCondition | None
    assigned_bin_id: uuid.UUID | None
    collection_session_id: uuid.UUID | None

    model_config = {"from_attributes": True}


# --- Collection sessions (Phase 4: collector doorstep flow) ---------------


class SessionBagInput(BaseModel):
    """One bag the collector is handing over as part of this session. If
    tag_id matches an existing bag it's reused (and reweighed); otherwise a
    new Bag is created with that tag — or, if tag_id is omitted (offline/no
    working QR on hand), a server-generated one. See DECISIONS.md."""

    bag_type: BagType
    tag_id: str | None = None
    gross_weight_kg: Decimal | None = None
    tare_weight_kg: Decimal | None = None
    bag_condition: BagCondition | None = None


class SessionCreate(BaseModel):
    user_id: uuid.UUID
    collector_id: uuid.UUID | None = None
    vehicle_code: str | None = None
    route_code: str | None = None
    gps_latitude: Decimal | None = None
    gps_longitude: Decimal | None = None
    notes: str | None = None
    # Set when this session fulfills a booked pickup — marks the request
    # COMPLETED and links it back to the session it produced.
    pickup_request_id: uuid.UUID | None = None
    bags: list[SessionBagInput] = []


class SessionArrive(BaseModel):
    """PATCH /sessions/{id}/arrive — defaults to now() if arrived_at is
    omitted, since the common case is the collector hitting the button on
    arrival, not backdating it."""

    arrived_at: dt.datetime | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    collected_at: dt.datetime
    collector_id: uuid.UUID | None
    vehicle_code: str | None
    route_code: str | None
    gps_latitude: Decimal | None
    gps_longitude: Decimal | None
    warehouse_arrival_at: dt.datetime | None
    notes: str | None

    model_config = {"from_attributes": True}


class SessionDetail(SessionOut):
    bags: list[BagOut] = []


class DetectionOut(BaseModel):
    id: uuid.UUID
    item_name: str
    subcategory: str | None
    category: str | None
    confidence: float
    estimated_quantity: str | None
    ocr_text: str | None
    matched_brand_id: uuid.UUID | None
    bbox: dict[str, Any] | None
    needs_review: bool
    review_status: ReviewStatus
    corrected_item_name: str | None
    # Phase 1 fields, exposed here so the review page (Phase 3) can render
    # them. bbox_x/y/w/h are None on every detection produced to date — the
    # vision pipeline doesn't estimate geometry yet — the review UI draws a
    # box only when all four are present.
    brand_text: str | None
    item_state: ItemState | None
    is_contaminant: bool
    count_est: int | None
    bbox_x: int | None
    bbox_y: int | None
    bbox_w: int | None
    bbox_h: int | None

    model_config = {"from_attributes": True}


class InferenceRunOut(BaseModel):
    """One vision-model call attempt for a capture (Phase 4: shown in full
    on the station page, not just the winning attempt, so operators can see
    why a repair retry happened)."""

    id: uuid.UUID
    attempt_no: int
    provider_name: str
    model_name: str
    status: InferenceRunStatus
    latency_ms: int | None
    overall_confidence: float | None
    error_message: str | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None

    model_config = {"from_attributes": True}


class CaptureOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    bag_id: uuid.UUID
    bag_type: BagType
    station_id: str
    captured_at: dt.datetime
    analysis_status: AnalysisStatus
    # --- Phase 4: upload-time provenance ---
    inspection_station_id: uuid.UUID | None = None
    tray_code: str | None = None
    lighting_condition: LightingCondition | None = None
    image_sha256: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    file_size_bytes: int | None = None

    model_config = {"from_attributes": True}


class CaptureDetail(CaptureOut):
    detections: list[DetectionOut] = []
    inference_runs: list[InferenceRunOut] = []
    # Not a plain from_attributes passthrough: the ORM column is an S3 key,
    # not a browser-fetchable URL. The endpoint overwrites this with a
    # presigned URL after validation — see api/v1/captures.py.
    image_url: str | None = None
