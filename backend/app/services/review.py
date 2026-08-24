"""The review workflow's business logic (Phase 3): which detections need a
human look, applying a reviewer's decision, and reviewer stats.

Closes the gap flagged when this phase was scoped: review_status was never
written anywhere, which silently weakened services/aggregation.py's
"trustworthy detection" rule to confidence-only. apply_review() below is
the only thing that writes review_status/corrected_item_name from here on.
"""

import datetime as dt
import uuid

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Capture,
    Detection,
    HumanReview,
    ReviewStatus,
    ReviewVerdict,
    StaffAccount,
)
from app.schemas.review import ReviewAction, ReviewerStat, ReviewStats
from app.services.audit import record
from app.services.storage import presigned_get_url

_VERDICT_TO_STATUS: dict[ReviewVerdict, ReviewStatus] = {
    ReviewVerdict.CONFIRMED: ReviewStatus.confirmed,
    ReviewVerdict.CORRECTED: ReviewStatus.corrected,
    ReviewVerdict.REJECTED: ReviewStatus.rejected,
}


def _queue_where_clause():
    """The four reasons a detection needs review, ORed together. A stable
    (not re-randomized per request) N% QA sample of high-confidence
    detections: whether a given detection is in the sample is a
    deterministic function of its own id, via Postgres's hashtext(), so it
    doesn't flicker in/out of the queue between page loads."""
    settings = get_settings()
    threshold = settings.confidence_review_threshold
    qa_percent = settings.review_qa_sample_percent

    low_confidence = Detection.confidence < threshold
    unidentified = Detection.item_name == "unidentified_item"
    unmatched_brand = (Detection.brand_text.isnot(None)) & (Detection.matched_brand_id.is_(None))
    qa_sample = (Detection.confidence >= threshold) & (
        func.abs(func.hashtext(cast(Detection.id, String))) % 100 < qa_percent
    )
    return Detection.review_status == ReviewStatus.unreviewed, or_(
        low_confidence, unidentified, unmatched_brand, qa_sample
    )


def _queue_reason(detection: Detection, threshold: float) -> str:
    """Human-readable version of the same rule, for display — not used for
    filtering (that's the SQL clause above), just to explain a queue item."""
    if detection.item_name == "unidentified_item":
        return "unidentified_item"
    if detection.brand_text and detection.matched_brand_id is None:
        return "unmatched_brand"
    if detection.confidence < threshold:
        return "low_confidence"
    return "qa_sample"


def count_review_queue(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Detection).where(*_queue_where_clause())) or 0


def build_review_queue(db: Session, limit: int, offset: int) -> list[dict]:
    """Oldest capture first. Returns plain dicts (not ORM/schema objects) —
    each combines a Detection with just enough of its Capture to review it
    without a second request per item; the router assembles these into
    ReviewQueueItem."""
    threshold = get_settings().confidence_review_threshold
    rows = db.execute(
        select(Detection, Capture)
        .join(Capture, Detection.capture_id == Capture.id)
        .where(*_queue_where_clause())
        .order_by(Capture.captured_at.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = []
    for detection, capture in rows:
        items.append(
            {
                "detection": detection,
                "capture_id": capture.id,
                "capture_image_url": presigned_get_url(capture.image_url),
                "capture_bag_type": capture.bag_type.value,
                "captured_at": capture.captured_at,
                "queue_reason": _queue_reason(detection, threshold),
            }
        )
    return items


def apply_review(
    db: Session, *, detection: Detection, reviewer: StaffAccount, action: ReviewAction
) -> HumanReview:
    """Persist one HumanReview and sync Detection.review_status/
    corrected_item_name to match — the model's original item_name is never
    touched, per Detection's own docstring."""
    review = HumanReview(
        detection_id=detection.id,
        reviewer_id=reviewer.id,
        verdict=action.verdict,
        corrected_item_name=action.corrected_item_name,
        corrected_brand_text=action.corrected_brand_text,
        corrected_count=action.corrected_count,
        corrected_is_contaminant=action.corrected_is_contaminant,
        notes=action.notes,
        time_spent_seconds=action.time_spent_seconds,
    )
    db.add(review)

    detection.review_status = _VERDICT_TO_STATUS[action.verdict]
    detection.needs_review = False
    detection.reviewed_by = reviewer.id
    detection.reviewed_at = dt.datetime.now(dt.UTC)
    if action.verdict == ReviewVerdict.CORRECTED and action.corrected_item_name:
        detection.corrected_item_name = action.corrected_item_name

    record(
        db,
        actor_id=reviewer.id,
        action="detection.review",
        entity_type="detection",
        entity_id=str(detection.id),
        detail={"verdict": action.verdict.value},
    )
    db.commit()
    db.refresh(review)
    return review


def compute_review_stats(db: Session) -> ReviewStats:
    """Today = UTC calendar day. agreement_rate = confirmed / total reviewed
    today — how often a reviewer left the model's own guess standing,
    versus overriding it (corrected) or throwing it out (rejected)."""
    today_start = dt.datetime.combine(dt.datetime.now(dt.UTC).date(), dt.time.min, tzinfo=dt.UTC)

    rows = db.execute(
        select(HumanReview.reviewer_id, StaffAccount.email, HumanReview.verdict, func.count())
        .join(StaffAccount, HumanReview.reviewer_id == StaffAccount.id)
        .where(HumanReview.reviewed_at >= today_start)
        .group_by(HumanReview.reviewer_id, StaffAccount.email, HumanReview.verdict)
    ).all()

    by_reviewer: dict[uuid.UUID, ReviewerStat] = {}
    for reviewer_id, email, verdict, count in rows:
        stat = by_reviewer.setdefault(
            reviewer_id,
            ReviewerStat(
                reviewer_id=reviewer_id,
                reviewer_email=email,
                reviewed_count=0,
                confirmed_count=0,
                corrected_count=0,
                rejected_count=0,
            ),
        )
        stat.reviewed_count += count
        if verdict == ReviewVerdict.CONFIRMED:
            stat.confirmed_count += count
        elif verdict == ReviewVerdict.CORRECTED:
            stat.corrected_count += count
        elif verdict == ReviewVerdict.REJECTED:
            stat.rejected_count += count

    reviewed_today = sum(s.reviewed_count for s in by_reviewer.values())
    confirmed_total = sum(s.confirmed_count for s in by_reviewer.values())
    agreement_rate = round(confirmed_total / reviewed_today, 4) if reviewed_today else 0.0

    return ReviewStats(
        reviewed_today=reviewed_today,
        agreement_rate=agreement_rate,
        by_reviewer=sorted(by_reviewer.values(), key=lambda s: -s.reviewed_count),
    )
