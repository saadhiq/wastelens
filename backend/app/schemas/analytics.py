"""Schemas for waste profiles and facility analytics."""

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel


class ProfileOut(BaseModel):
    user_id: uuid.UUID
    week_start: dt.date
    veg_frequency: int
    top_vegetables: list[Any]
    packaged_food_frequency: int
    top_brands: list[Any]
    category_breakdown: dict[str, Any]
    rebuilt_at: dt.datetime

    model_config = {"from_attributes": True}


class RebuildResult(BaseModel):
    profiles_written: int
    weeks_back: int


class ItemCount(BaseModel):
    name: str
    count: int


class QualityByItem(BaseModel):
    item_name: str
    detections: int
    avg_confidence: float
    reviewed: int
    corrected: int


class QualityReport(BaseModel):
    total_detections: int
    avg_confidence: float
    pct_needs_review: float
    capture_failure_rate: float
    by_item: list[QualityByItem]
