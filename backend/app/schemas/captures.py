"""Schemas for bags, collection sessions, captures, and detections."""

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel

from app.models.base import AnalysisStatus, BagStatus, BagType, ReviewStatus


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

    model_config = {"from_attributes": True}


class SessionCreate(BaseModel):
    user_id: uuid.UUID


class SessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    collected_at: dt.datetime

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


class CaptureOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    bag_id: uuid.UUID
    bag_type: BagType
    station_id: str
    captured_at: dt.datetime
    analysis_status: AnalysisStatus

    model_config = {"from_attributes": True}


class CaptureDetail(CaptureOut):
    detections: list[DetectionOut] = []
