"""Fuzzy brand matching: brand text read off packaging → brands table.

Uses rapidfuzz partial matching against brand names and aliases; the threshold
is configurable (BRAND_MATCH_THRESHOLD, 0-100). Both the raw text and the
matched brand are stored on the detection.

Phase 5: when nothing matches above threshold, the text isn't discarded —
record_unmapped_brand tracks it as a BRAND-kind UnmappedLabel, the same
"don't lose it" treatment ITEM-kind unmapped labels get. A repeatedly-seen
unmatched brand is exactly how the Brand catalogue is meant to grow (see
GET /analytics/unmapped-brands).
"""

import uuid

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BagType, Brand, UnmappedLabel, UnmappedLabelKind


def match_brand(db: Session, text: str | None) -> uuid.UUID | None:
    """Return the best-matching brand id, or None below threshold."""
    if not text or not text.strip():
        return None

    threshold = get_settings().brand_match_threshold
    needle = text.lower()

    best_id: uuid.UUID | None = None
    best_score = 0.0
    for brand in db.scalars(select(Brand)).all():
        candidates = [brand.name, *brand.aliases]
        for candidate in candidates:
            score = fuzz.partial_ratio(candidate.lower(), needle)
            if score > best_score:
                best_score = score
                best_id = brand.id

    return best_id if best_score >= threshold else None


def record_unmapped_brand(db: Session, brand_text: str | None, bag_type: BagType) -> None:
    """Track a brand_text that didn't match any Brand above threshold.
    Idempotent-ish per (brand_text, bag_type): repeats bump
    occurrence_count/last_seen_at on the same row rather than growing a new
    one per sighting, so GET /analytics/unmapped-brands ranks by real
    frequency."""
    if not brand_text or not brand_text.strip():
        return

    existing = db.scalar(
        select(UnmappedLabel).where(
            UnmappedLabel.raw_label == brand_text,
            UnmappedLabel.bag_type == bag_type,
            UnmappedLabel.label_kind == UnmappedLabelKind.BRAND,
        )
    )
    if existing is not None:
        existing.occurrence_count += 1
        existing.last_seen_at = func.now()
    else:
        db.add(
            UnmappedLabel(
                raw_label=brand_text,
                bag_type=bag_type,
                label_kind=UnmappedLabelKind.BRAND,
            )
        )
