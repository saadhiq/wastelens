"""The CV analysis orchestrator — the heart of the Phase 1 pipeline, extended
in Phase 2 to persist one InferenceRun row per provider call attempt.

Called by the Celery task for one capture:
  load capture → download image → load vocabulary (from DB) → cost guard →
  provider.analyze (→ InferenceRun row) → validate strict JSON (one repair
  retry, its own InferenceRun row) → fuzzy-match brands → write detections
  (linked to whichever InferenceRun produced them) + review flags → mark
  capture done.

On unrecoverable model output the capture is marked `failed` and the raw text
is preserved in a single detection row for debugging.
"""

import datetime as dt
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import (
    AnalysisStatus,
    Bag,
    BagStatus,
    Capture,
    Detection,
    InferenceRun,
    InferenceRunStatus,
    VocabularyItem,
)
from app.services import storage
from app.services.barcode import decode_barcodes
from app.services.brand_match import match_brand, record_unmapped_brand
from app.services.cost_guard import CostCapExceeded, register_cv_call
from app.services.vision import VisionProvider, VisionResult, get_vision_provider
from app.services.vision.base import ProviderResponse
from app.services.vision.prompts import get_prompt_version

log = get_logger(__name__)


def _configured_model_name(settings: Settings) -> str:
    """Best-effort model identifier for an InferenceRun row written when the
    provider call itself raised — before any real response.model exists."""
    return (
        settings.nvidia_vision_model
        if settings.vision_provider.lower() == "nvidia"
        else settings.vision_model
    )


def _overall_confidence(result: VisionResult) -> float | None:
    if not result.detections:
        return None
    return sum(d.confidence for d in result.detections) / len(result.detections)


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


def _next_attempt_no(db: Session, capture_id: uuid.UUID) -> int:
    """1 for a fresh capture; higher if this capture already has InferenceRun
    rows (a Celery retry re-entering after a prior attempt was recorded).
    Never reuses an attempt_no — that's what the (capture_id, attempt_no)
    unique constraint depends on for idempotency."""
    highest = db.scalar(
        select(func.max(InferenceRun.attempt_no)).where(InferenceRun.capture_id == capture_id)
    )
    return (highest or 0) + 1


def _classify_call_failure(exc: Exception) -> InferenceRunStatus:
    if "timeout" in type(exc).__name__.lower():
        return InferenceRunStatus.TIMEOUT
    return InferenceRunStatus.FAILED_PROVIDER_ERROR


def _run_attempt(
    db: Session,
    capture: Capture,
    provider: VisionProvider,
    image_bytes: bytes,
    media_type: str,
    vocabulary: list[str],
    *,
    attempt_no: int,
    prior_invalid_output: str | None,
    call_count: int,
) -> tuple[InferenceRun, ProviderResponse, VisionResult | None]:
    """Make one provider.analyze() call and persist its InferenceRun row.

    Returns (run, response, parsed_result) — parsed_result is None when the
    model's output didn't parse as valid JSON (status FAILED_INVALID_JSON).
    If the provider call itself raises (network/provider error, timeout),
    this function doesn't return at all: it writes the InferenceRun row
    (status FAILED_PROVIDER_ERROR/TIMEOUT) and re-raises, so that failure is
    never silently lost.
    """
    settings = get_settings()
    started_at = dt.datetime.now(dt.UTC)
    # A pure function of bag_type (see prompts.py) — computed here directly
    # rather than threaded through ProviderResponse, since every provider
    # builds its prompt from the same build_analysis_prompt(bag_type, ...)
    # and there's no scenario where the two could disagree. This also means
    # it's known even when the provider call below raises before returning.
    prompt_version = get_prompt_version(capture.bag_type)
    try:
        response = provider.analyze(
            image_bytes,
            media_type,
            capture.bag_type,
            vocabulary,
            prior_invalid_output=prior_invalid_output,
        )
    except Exception as exc:
        run = InferenceRun(
            capture_id=capture.id,
            attempt_no=attempt_no,
            provider_name=settings.vision_provider,
            model_name=_configured_model_name(settings),
            prompt_version=prompt_version,
            status=_classify_call_failure(exc),
            error_message=str(exc),
            started_at=started_at,
            finished_at=dt.datetime.now(dt.UTC),
        )
        db.add(run)
        db.commit()
        log.warning(
            "vision_call_failed",
            capture_id=str(capture.id),
            attempt_no=attempt_no,
            status=run.status.value,
            error=str(exc),
        )
        raise

    try:
        result: VisionResult | None = parse_vision_result(response.raw_text)
        status = InferenceRunStatus.SUCCESS
        error_message = None
    except (ValueError, ValidationError) as exc:
        result = None
        status = InferenceRunStatus.FAILED_INVALID_JSON
        error_message = str(exc)

    run = InferenceRun(
        capture_id=capture.id,
        attempt_no=attempt_no,
        provider_name=settings.vision_provider,
        model_name=response.model,
        model_version=response.model_version,
        prompt_version=prompt_version,
        status=status,
        latency_ms=response.latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
        overall_confidence=_overall_confidence(result) if result is not None else None,
        raw_response=response.raw_response,
        raw_text=response.raw_text,
        error_message=error_message,
        started_at=started_at,
        finished_at=dt.datetime.now(dt.UTC),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    log.info(
        "vision_call",
        capture_id=str(capture.id),
        attempt_no=attempt_no,
        model=response.model,
        latency_ms=response.latency_ms,
        usage=response.usage,
        daily_call_count=call_count,
        outcome=status.value,
    )
    return run, response, result


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
        attempt_no = _next_attempt_no(db, capture.id)

        call_count = register_cv_call()
        run, response, result = _run_attempt(
            db,
            capture,
            provider,
            image_bytes,
            media_type,
            vocabulary,
            attempt_no=attempt_no,
            prior_invalid_output=None,
            call_count=call_count,
        )

        if result is None:
            # One repair round: show the model its own invalid output.
            call_count = register_cv_call()
            run, response, result = _run_attempt(
                db,
                capture,
                provider,
                image_bytes,
                media_type,
                vocabulary,
                attempt_no=attempt_no + 1,
                prior_invalid_output=response.raw_text,
                call_count=call_count,
            )
            if result is None:
                _fail_capture(db, capture, raw_output=response.raw_text, inference_run_id=run.id)
                return

        _store_detections(
            db,
            capture,
            result,
            raw_model_output=response.raw_text,
            inference_run_id=run.id,
            image_bytes=image_bytes,
        )
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
    db: Session,
    capture: Capture,
    result: VisionResult,
    raw_model_output: str,
    inference_run_id: uuid.UUID,
    image_bytes: bytes,
) -> None:
    threshold = get_settings().confidence_review_threshold
    vocabulary = set(load_vocabulary(db, capture.bag_type))
    raw = {"raw_text": raw_model_output, "tray_notes": result.tray_notes}

    # Barcode: a dedicated decode pass on the whole tray image, independent
    # of the vision model — see services/barcode.py. Decoded values are
    # ground truth and beat whatever the model read into barcode_text via
    # OCR, so they're claimed first, in the order both lists were reported
    # (roughly left-to-right on the tray for zbar; listing order for the
    # model). This is a best-effort positional pairing, not a spatial
    # match — the model doesn't reliably return bbox today, so there's no
    # sturdier signal to match on yet. See DECISIONS.md.
    decoded_barcodes = decode_barcodes(image_bytes)
    decoded_idx = 0

    for item in result.detections:
        # Out-of-vocabulary names are kept but demoted to unidentified_item so
        # downstream aggregation only ever sees known labels.
        item_name = item.item_name if item.item_name in vocabulary else "unidentified_item"
        subcategory = item.subcategory
        if item_name != item.item_name:
            subcategory = f"model said: {item.item_name}" + (
                f" | {subcategory}" if subcategory else ""
            )

        if decoded_idx < len(decoded_barcodes):
            barcode_text = decoded_barcodes[decoded_idx]
            barcode_source = "decoded"
            decoded_idx += 1
        elif item.barcode_text:
            barcode_text = item.barcode_text
            barcode_source = "ocr"
        else:
            barcode_text = None
            barcode_source = None

        # brand_text is the v2-prompt field; ocr_text is the v1 fallback so
        # organic/general (and any packaging item the model only OCR'd
        # generically) still get a shot at matching a known brand.
        brand_source_text = item.brand_text or item.ocr_text
        matched_brand_id = match_brand(db, brand_source_text)
        if matched_brand_id is None and item.brand_text:
            record_unmapped_brand(db, item.brand_text, capture.bag_type)

        item_raw = {**raw, "barcode_source": barcode_source}

        db.add(
            Detection(
                capture_id=capture.id,
                item_name=item_name,
                subcategory=subcategory,
                category=capture.bag_type.value,
                confidence=item.confidence,
                estimated_quantity=item.estimated_quantity,
                ocr_text=item.ocr_text,
                brand_text=item.brand_text,
                product_name_text=item.product_name_text,
                pack_size_text=item.pack_size_text,
                barcode_text=barcode_text,
                material_type=item.material_type,
                matched_brand_id=matched_brand_id,
                bbox=item.bbox,
                needs_review=item.confidence < threshold,
                raw_model_output=item_raw,
                inference_run_id=inference_run_id,
            )
        )


def _fail_capture(
    db: Session, capture: Capture, raw_output: str, inference_run_id: uuid.UUID | None = None
) -> None:
    """Mark failed and keep the raw output on a placeholder detection row so
    engineers can see exactly what the model produced. inference_run_id is
    None only for the cost-cap-exceeded path, where no provider call (and so
    no InferenceRun) ever happened."""
    capture.analysis_status = AnalysisStatus.failed
    db.add(
        Detection(
            capture_id=capture.id,
            item_name="unidentified_item",
            confidence=0.0,
            needs_review=True,
            raw_model_output={"raw_text": raw_output, "error": "unparseable_model_output"},
            inference_run_id=inference_run_id,
        )
    )
    db.commit()
    log.warning("capture_failed", capture_id=str(capture.id))
