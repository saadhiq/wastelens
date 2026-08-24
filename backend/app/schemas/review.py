"""Schemas for the review workflow (Phase 3): reviewing individual/bulk
detections, the review queue, and reviewer stats."""

import datetime as dt
import uuid

from pydantic import BaseModel, model_validator

from app.models.base import ReviewVerdict
from app.schemas.captures import DetectionOut


class ReviewAction(BaseModel):
    """Body for POST /detections/{id}/review."""

    verdict: ReviewVerdict
    corrected_item_name: str | None = None
    corrected_brand_text: str | None = None
    corrected_count: int | None = None
    corrected_is_contaminant: bool | None = None
    notes: str | None = None
    time_spent_seconds: int | None = None

    @model_validator(mode="after")
    def _corrected_requires_a_correction(self) -> "ReviewAction":
        if self.verdict == ReviewVerdict.CORRECTED and not any(
            (
                self.corrected_item_name,
                self.corrected_brand_text,
                self.corrected_count is not None,
                self.corrected_is_contaminant is not None,
            )
        ):
            raise ValueError("verdict=CORRECTED requires at least one corrected_* field")
        return self


class HumanReviewOut(BaseModel):
    id: uuid.UUID
    detection_id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewed_at: dt.datetime
    verdict: ReviewVerdict
    corrected_item_name: str | None
    corrected_brand_text: str | None
    corrected_count: int | None
    corrected_is_contaminant: bool | None
    notes: str | None
    time_spent_seconds: int | None

    model_config = {"from_attributes": True}


class BulkReviewRequest(BaseModel):
    """Body for POST /detections/bulk-review — the common "confirm everything
    above X% confidence" workflow. All listed detections get the same
    verdict (default CONFIRMED); use the single-detection endpoint for
    per-item corrections."""

    detection_ids: list[uuid.UUID]
    verdict: ReviewVerdict = ReviewVerdict.CONFIRMED


class BulkReviewResult(BaseModel):
    reviewed: int
    skipped: list[uuid.UUID]  # ids that didn't exist or were already reviewed by this call


class ReviewQueueItem(DetectionOut):
    """A queued detection plus just enough capture context to review it
    without a second request per item."""

    capture_id: uuid.UUID
    capture_image_url: str
    capture_bag_type: str
    captured_at: dt.datetime
    queue_reason: str


class ReviewerStat(BaseModel):
    reviewer_id: uuid.UUID
    reviewer_email: str
    reviewed_count: int
    confirmed_count: int
    corrected_count: int
    rejected_count: int


class ReviewStats(BaseModel):
    reviewed_today: int
    agreement_rate: float  # confirmed / (confirmed + corrected + rejected), today
    by_reviewer: list[ReviewerStat]
