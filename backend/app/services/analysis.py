"""The CV analysis orchestrator — the heart of the Phase 1 pipeline.

Called by the Celery task for one capture:
  load capture → download image → load vocabulary (from DB) → cost guard →
  provider.analyze → validate strict JSON (one repair retry) →
  fuzzy-match brands → write detections + review flags → mark capture done.

On unrecoverable model output the capture is marked `failed` and the raw text
is preserved in a single detection row for debugging.
"""

import json
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models import (
    AnalysisStatus,
    Bag,
    BagStatus,
    Capture,
    Detection,
    VocabularyItem,
)
from app.services import storage
from app.services.brand_match import match_brand
from app.services.cost_guard import CostCapExceeded, register_cv_call
from app.services.vision import VisionProvider, VisionResult, get_vision_provider

log = get_logger(__name__)


def _strip_code_fences(text: str) -> str:
    """Models sometimes wrap JSON in ```json fences despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def parse_vision_result(raw_text: str) -> VisionResult:
    """Parse+validate model output; raises ValueError/ValidationError on failure."""
    return VisionResult.model_validate(json.loads(_strip_code_fences(raw_text)))


def load_vocabulary(db: Session, bag_type) -> list[str]:
    return list(
        db.scalars(
            select(VocabularyItem.item_name).where(
                VocabularyItem.bag_type == bag_type,
                VocabularyItem.active.is_(True),
            )
        )
    )


def analyze_capture(
    db: Session, capture_id: uuid.UUID, provider: VisionProvider | None = None
) -> None:
    """Run the full analysis for one capture. Idempotent-ish: does nothing if
    the capture is already done."""
    capture = db.get(Capture, capture_id)
    if capture is None:
        log.warning("capture_missing", capture_id=str(capture_id))
        return
    if capture.analysis_status == AnalysisStatus.done:
        log.info("capture_already_done", capture_id=str(capture_id))
        return

    capture.analysis_status = AnalysisStatus.processing
    db.commit()

    try:
        vocabulary = load_vocabulary(db, capture.bag_type)
        if not vocabulary:
            raise RuntimeError(f"No active vocabulary for bag_type={capture.bag_type}")

        image_bytes, media_type = storage.download_image(capture.image_url)
        provider = provider or get_vision_provider()

        call_count = register_cv_call()
        response = provider.analyze(image_bytes, media_type, capture.bag_type, vocabulary)
        log.info(
            "vision_call",
            capture_id=str(capture_id),
            model=response.model,
            latency_ms=response.latency_ms,
            usage=response.usage,
            daily_call_count=call_count,
            outcome="ok",
        )

        try:
            result = parse_vision_result(response.raw_text)
        except (ValueError, ValidationError):
            # One repair round: show the model its own invalid output.
            register_cv_call()
            repair = provider.analyze(
                image_bytes,
                media_type,
                capture.bag_type,
                vocabulary,
                prior_invalid_output=response.raw_text,
            )
            log.info(
                "vision_call",
                capture_id=str(capture_id),
                model=repair.model,
                latency_ms=repair.latency_ms,
                usage=repair.usage,
                outcome="repair_attempt",
            )
            try:
                result = parse_vision_result(repair.raw_text)
                response = repair
            except (ValueError, ValidationError):
                _fail_capture(db, capture, raw_output=repair.raw_text)
                return

        _store_detections(db, capture, result, raw_model_output=response.raw_text)
        capture.analysis_status = AnalysisStatus.done
        bag = db.get(Bag, capture.bag_id)
        if bag is not None:
            bag.status = BagStatus.processed
        db.commit()
        log.info(
            "analysis_done",
            capture_id=str(capture_id),
            detections=len(result.detections),
        )

    except CostCapExceeded as exc:
        db.rollback()
        _fail_capture(db, capture, raw_output=f"cost_cap_exceeded: {exc}")
        log.warning("cost_cap_exceeded", capture_id=str(capture_id), cap=exc.cap)
    except Exception:
        db.rollback()
        capture = db.get(Capture, capture_id)
        if capture is not None:
            capture.analysis_status = AnalysisStatus.failed
            db.commit()
        log.error("analysis_failed", capture_id=str(capture_id), exc_info=True)
        raise


def _store_detections(
    db: Session, capture: Capture, result: VisionResult, raw_model_output: str
) -> None:
    threshold = get_settings().confidence_review_threshold
    vocabulary = set(load_vocabulary(db, capture.bag_type))
    raw = {"raw_text": raw_model_output, "tray_notes": result.tray_notes}

    for item in result.detections:
        # Out-of-vocabulary names are kept but demoted to unidentified_item so
        # downstream aggregation only ever sees known labels.
        item_name = item.item_name if item.item_name in vocabulary else "unidentified_item"
        subcategory = item.subcategory
        if item_name != item.item_name:
            subcategory = f"model said: {item.item_name}" + (
                f" | {subcategory}" if subcategory else ""
            )

        db.add(
            Detection(
                capture_id=capture.id,
                item_name=item_name,
                subcategory=subcategory,
                category=capture.bag_type.value,
                confidence=item.confidence,
                estimated_quantity=item.estimated_quantity,
                ocr_text=item.ocr_text,
                matched_brand_id=match_brand(db, item.ocr_text),
                bbox=item.bbox,
                needs_review=item.confidence < threshold,
                raw_model_output=raw,
            )
        )


def _fail_capture(db: Session, capture: Capture, raw_output: str) -> None:
    """Mark failed and keep the raw output on a placeholder detection row so
    engineers can see exactly what the model produced."""
    capture.analysis_status = AnalysisStatus.failed
    db.add(
        Detection(
            capture_id=capture.id,
            item_name="unidentified_item",
            confidence=0.0,
            needs_review=True,
            raw_model_output={"raw_text": raw_output, "error": "unparseable_model_output"},
        )
    )
    db.commit()
    log.warning("capture_failed", capture_id=str(capture.id))
