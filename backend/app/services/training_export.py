"""JSONL training-data export (Phase 7): one line per reviewed capture,
holding the S3 image key and every human-verified detection on it — the
fine-tuning set for LocalYoloProvider.

Only CONFIRMED/CORRECTED detections are included — a REJECTED detection has
no valid label to train on, and an unreviewed one hasn't been human-
verified at all, so neither belongs in a "human-corrected detections" set.
A capture with zero surviving detections after that filter is omitted
entirely (a training example with an empty label list isn't useful).

Gated on consent_operational, not consent_profiling: training the CV
pipeline is an operational function of the system (the same bucket as "we
photograph and process your waste" — consent_operational's original
purpose), not the behavioral/consumer-insight use Phase 6's
consent_profiling gate exists for. Sensitive vocabulary items are still
excluded regardless of consent basis — see DECISIONS.md.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import Capture, CollectionSession, Detection, Resident, ReviewStatus, VocabularyItem
from app.services.aggregation import effective_item_name


def _reviewed_detections_query() -> Any:
    effective_name = func.coalesce(Detection.corrected_item_name, Detection.item_name)
    return (
        select(Detection, Capture)
        .join(Capture, Detection.capture_id == Capture.id)
        .join(CollectionSession, Capture.session_id == CollectionSession.id)
        .join(Resident, CollectionSession.user_id == Resident.id)
        .outerjoin(
            VocabularyItem,
            and_(
                VocabularyItem.bag_type == Capture.bag_type,
                VocabularyItem.item_name == effective_name,
            ),
        )
        .where(
            Resident.consent_operational.is_(True),
            Detection.review_status.in_((ReviewStatus.confirmed, ReviewStatus.corrected)),
            or_(VocabularyItem.is_sensitive.is_(False), VocabularyItem.id.is_(None)),
        )
        .order_by(Capture.id)
    )


def iter_training_examples(db: Session) -> Iterator[dict[str, Any]]:
    """One example per capture that has at least one human-verified,
    non-sensitive, operationally-consented detection."""
    rows = db.execute(_reviewed_detections_query()).all()

    by_capture: dict[uuid.UUID, dict[str, Any]] = {}
    for detection, capture in rows:
        entry = by_capture.setdefault(capture.id, {"capture": capture, "detections": []})
        entry["detections"].append(detection)

    for entry in by_capture.values():
        example_capture: Capture = entry["capture"]
        yield {
            "capture_id": str(example_capture.id),
            "image_key": example_capture.image_url,
            "bag_type": example_capture.bag_type.value,
            "detections": [
                {
                    "item_name": effective_item_name(d),
                    "confidence": d.confidence,
                    "brand_text": d.brand_text,
                    "product_name_text": d.product_name_text,
                    "pack_size_text": d.pack_size_text,
                    "material_type": d.material_type,
                    "bbox": d.bbox,
                    "bbox_x": d.bbox_x,
                    "bbox_y": d.bbox_y,
                    "bbox_w": d.bbox_w,
                    "bbox_h": d.bbox_h,
                }
                for d in entry["detections"]
            ],
        }


def to_jsonl(db: Session) -> bytes:
    lines = [json.dumps(example) for example in iter_training_examples(db)]
    body = "\n".join(lines)
    if lines:
        body += "\n"
    return body.encode("utf-8")
