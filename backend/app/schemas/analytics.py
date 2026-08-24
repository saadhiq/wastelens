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


class QualityByPromptVersion(BaseModel):
    """Phase 5: accuracy broken down by which prompts.py contract (and
    model) produced the detection — lets Phase 5's v2 packaging prompt be
    compared against v1 once enough reviewed data exists.

    accuracy = confirmed / reviewed, same convention as
    services/review.py's ReviewStats.agreement_rate: how often a reviewer
    left the model's own guess standing rather than correcting or
    rejecting it. 0.0 when nothing in the group has been reviewed yet —
    not None, since every consumer of this report expects a plain float.
    """

    prompt_version: str | None
    model_name: str
    detections: int
    avg_confidence: float
    reviewed: int
    confirmed: int
    corrected: int
    rejected: int
    accuracy: float


class QualityReport(BaseModel):
    total_detections: int
    avg_confidence: float
    pct_needs_review: float
    capture_failure_rate: float
    by_item: list[QualityByItem]
    by_prompt_version: list[QualityByPromptVersion]


class UnmappedBrandCount(BaseModel):
    raw_label: str
    bag_type: str
    occurrence_count: int
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
