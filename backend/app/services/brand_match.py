"""Fuzzy brand matching: OCR text from packaging → brands table.

Uses rapidfuzz partial matching against brand names and aliases; the threshold
is configurable (BRAND_MATCH_THRESHOLD, 0-100). Both the raw OCR text and the
matched brand are stored on the detection.
"""

import uuid

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Brand


def match_brand(db: Session, ocr_text: str | None) -> uuid.UUID | None:
    """Return the best-matching brand id, or None below threshold."""
    if not ocr_text or not ocr_text.strip():
        return None

    threshold = get_settings().brand_match_threshold
    text = ocr_text.lower()

    best_id: uuid.UUID | None = None
    best_score = 0.0
    for brand in db.scalars(select(Brand)).all():
        candidates = [brand.name, *brand.aliases]
        for candidate in candidates:
            score = fuzz.partial_ratio(candidate.lower(), text)
            if score > best_score:
                best_score = score
                best_id = brand.id

    return best_id if best_score >= threshold else None
