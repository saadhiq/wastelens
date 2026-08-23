"""Weekly waste-profile aggregation (Phase 3).

Rolls detections up into user_waste_profiles, one row per (resident, ISO week).

Quality rule — a detection counts toward profiles only if it is trustworthy:
    confidence >= CONFIDENCE_REVIEW_THRESHOLD
    OR review_status in (confirmed, corrected)
and never when review_status = rejected. When a reviewer corrected the label,
corrected_item_name wins over the model's guess. This rule holds whether or
not the review console has been used yet.
"""

import datetime as dt
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models import (
    BagType,
    Brand,
    Capture,
    CollectionSession,
    Detection,
    ReviewStatus,
    UserWasteProfile,
)

log = get_logger(__name__)

# Categories whose (non-unidentified) items count as packaged food.
_PACKAGED_CATEGORIES = {BagType.polythene.value, BagType.paper.value}


def week_start_of(moment: dt.datetime) -> dt.date:
    """Monday of the ISO week containing `moment` (UTC)."""
    d = moment.date()
    return d - dt.timedelta(days=d.weekday())


def effective_item_name(detection: Detection) -> str:
    return detection.corrected_item_name or detection.item_name


def is_trustworthy(detection: Detection, threshold: float) -> bool:
    if detection.review_status == ReviewStatus.rejected:
        return False
    if detection.review_status in (ReviewStatus.confirmed, ReviewStatus.corrected):
        return True
    return detection.confidence >= threshold


@dataclass
class _Bucket:
    veg: Counter = field(default_factory=Counter)
    brands: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    packaged: int = 0


def rebuild_week(db: Session, week_start: dt.date) -> int:
    """Rebuild profiles for every resident with activity in the given week.
    Returns the number of profile rows written."""
    threshold = get_settings().confidence_review_threshold
    week_end = week_start + dt.timedelta(days=7)

    rows = db.execute(
        select(Detection, Capture.captured_at, CollectionSession.user_id)
        .join(Capture, Detection.capture_id == Capture.id)
        .join(CollectionSession, Capture.session_id == CollectionSession.id)
        .where(Capture.captured_at >= week_start, Capture.captured_at < week_end)
    ).all()

    brand_names = {b.id: b.name for b in db.scalars(select(Brand)).all()}

    buckets: dict[uuid.UUID, _Bucket] = defaultdict(_Bucket)
    for detection, _captured_at, user_id in rows:
        if not is_trustworthy(detection, threshold):
            continue
        name = effective_item_name(detection)
        category = detection.category or "unknown"
        bucket = buckets[user_id]
        bucket.categories[category] += 1
        if name == "unidentified_item":
            continue
        if category == BagType.organic.value:
            bucket.veg[name] += 1
        elif category in _PACKAGED_CATEGORIES:
            bucket.packaged += 1
        if detection.matched_brand_id and detection.matched_brand_id in brand_names:
            bucket.brands[brand_names[detection.matched_brand_id]] += 1

    written = 0
    for user_id, bucket in buckets.items():
        profile = db.scalar(
            select(UserWasteProfile).where(
                UserWasteProfile.user_id == user_id,
                UserWasteProfile.week_start == week_start,
            )
        )
        if profile is None:
            profile = UserWasteProfile(user_id=user_id, week_start=week_start)
            db.add(profile)
        profile.veg_frequency = sum(bucket.veg.values())
        profile.top_vegetables = [
            {"item": item, "count": count} for item, count in bucket.veg.most_common(10)
        ]
        profile.packaged_food_frequency = bucket.packaged
        profile.top_brands = [
            {"brand": brand, "count": count} for brand, count in bucket.brands.most_common(10)
        ]
        profile.category_breakdown = dict(bucket.categories)
        written += 1

    db.commit()
    log.info("profiles_rebuilt", week_start=str(week_start), profiles=written)
    return written


def rebuild_recent(db: Session, weeks_back: int = 2) -> int:
    """Rebuild the current week plus `weeks_back - 1` previous weeks —
    detections reviewed late still land in the right historical week."""
    today_week = week_start_of(dt.datetime.now(dt.UTC))
    total = 0
    for i in range(weeks_back):
        total += rebuild_week(db, today_week - dt.timedelta(weeks=i))
    return total
