"""Schemas for waste profiles and facility analytics."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models.base import AlertType, ConsumptionSignalSubjectType


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


# --- Phase 6: household consumption layer ----------------------------------


class ConsumptionSignalOut(BaseModel):
    id: uuid.UUID
    subject_type: ConsumptionSignalSubjectType
    subject_value: str
    category: str
    disposal_dates: list[Any]
    replenishment_cycle_days_mean: Decimal | None
    replenishment_cycle_days_stddev: Decimal | None
    last_disposal_date: dt.date | None
    predicted_next_disposal_date: dt.date | None
    observation_count: int
    confidence: Decimal | None

    model_config = {"from_attributes": True}


class BrandShare(BaseModel):
    brand: str
    share: float
    observation_count: int


class CategoryBrandLoyalty(BaseModel):
    brand_shares: list[BrandShare]
    herfindahl_index: float


class ConsumptionOut(BaseModel):
    resident_id: uuid.UUID
    category_signals: list[ConsumptionSignalOut]
    brand_signals: list[ConsumptionSignalOut]
    # Keyed by category. Absent entries mean no brand-matched detections
    # were observed for that category — not zero loyalty, just no data.
    brand_loyalty: dict[str, CategoryBrandLoyalty]
    # Share (0-1) of packaged+fresh trustworthy detections that are
    # packaged; null when there's neither to compare.
    packaged_vs_fresh_ratio: float | None
    # Share (0-1) of organic detections with a known item_state that are
    # SPOILED/MOULDY; null when no organic detection has a known state.
    spoiled_food_share: float | None


class BrandSwitchEvent(BaseModel):
    resident_id: uuid.UUID
    category: str
    brand_from: str
    brand_to: str
    brand_from_last_seen: dt.date
    brand_to_first_seen: dt.date


class ChurnRiskItem(BaseModel):
    resident_id: uuid.UUID
    category: str
    last_disposal_date: dt.date
    days_since_last_disposal: int
    expected_cycle_days: float


class AlertOut(BaseModel):
    id: uuid.UUID
    alert_type: AlertType
    message: str
    metric_value: Decimal
    threshold: Decimal
    created_at: dt.datetime

    model_config = {"from_attributes": True}
