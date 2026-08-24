"""Waste profiles and facility analytics (analyst/admin only, anonymized —
these endpoints expose user_id but never name/phone/address)."""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import (
    Alert,
    AnalysisStatus,
    BagType,
    Brand,
    Capture,
    Detection,
    InferenceRun,
    ReviewStatus,
    StaffAccount,
    StaffRole,
    UnmappedLabel,
    UnmappedLabelKind,
    UserWasteProfile,
)
from app.schemas.analytics import (
    AlertOut,
    BrandSwitchEvent,
    ChurnRiskItem,
    ConsumptionOut,
    ConsumptionSignalOut,
    ItemCount,
    ProfileOut,
    QualityByItem,
    QualityByPromptVersion,
    QualityReport,
    RebuildResult,
    UnmappedBrandCount,
)
from app.services.aggregation import rebuild_recent
from app.services.audit import record
from app.services.exports import (
    gated_profile_rows,
    gated_quality_rows,
    gated_top_brands,
    gated_top_items,
    to_csv,
    to_pdf,
)
from app.services.profiling import (
    detect_brand_switches,
    detect_churn_risk,
    get_consumption,
    get_predictions,
)

router = APIRouter(tags=["analytics"])

_ANALYTICS_ROLES = (StaffRole.analyst,)


def _since(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


def _export_response(
    export_format: str, filename_stem: str, title: str, headers: list[str], rows: list
) -> Response:
    """CSV/PDF variant of a report (Phase 7) — the JSON default is
    returned by the caller itself via the normal response_model path, this
    is only reached for format=csv/pdf."""
    if export_format == "csv":
        content: bytes = to_csv(headers, rows)
        media_type = "text/csv"
    else:
        content = to_pdf(title, headers, rows)
        media_type = "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename_stem}.{export_format}"'},
    )


@router.get("/profiles/{user_id}", response_model=list[ProfileOut])
def get_user_profiles(
    user_id: uuid.UUID,
    weeks: int = Query(12, ge=1, le=104),
    export_format: str = Query("json", alias="format", pattern="^(json|csv|pdf)$"),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[UserWasteProfile] | Response:
    """A household's weekly waste-profile timeline, newest first.

    format=csv/pdf (Phase 7) additionally gates on the household's current
    consent — unlike the JSON path above, which is unchanged pre-Phase-6
    behavior. See services/exports.py."""
    if export_format == "json":
        return list(
            db.scalars(
                select(UserWasteProfile)
                .where(UserWasteProfile.user_id == user_id)
                .order_by(UserWasteProfile.week_start.desc())
                .limit(weeks)
            )
        )

    profiles = gated_profile_rows(db, user_id, weeks)
    if profiles is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    headers = [
        "week_start",
        "veg_frequency",
        "packaged_food_frequency",
        "top_vegetables",
        "top_brands",
    ]
    rows = [
        [p.week_start, p.veg_frequency, p.packaged_food_frequency, p.top_vegetables, p.top_brands]
        for p in profiles
    ]
    return _export_response(
        export_format, f"profile-{user_id}", f"Waste profile — {user_id}", headers, rows
    )


@router.post("/profiles/rebuild", response_model=RebuildResult)
def rebuild_profiles(
    weeks_back: int = Query(2, ge=1, le=52),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> RebuildResult:
    """Rebuild profiles on demand (the same job also runs nightly). Runs
    synchronously — data volumes are small; revisit if this ever gets slow."""
    written = rebuild_recent(db, weeks_back=weeks_back)
    record(db, actor_id=account.id, action="profiles.rebuild", detail={"weeks_back": weeks_back})
    db.commit()
    return RebuildResult(profiles_written=written, weeks_back=weeks_back)


@router.get("/analytics/top-items", response_model=list[ItemCount])
def top_items(
    bag_type: BagType | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    export_format: str = Query("json", alias="format", pattern="^(json|csv|pdf)$"),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[ItemCount] | Response:
    """Most frequent (trustworthy) items across all households.

    format=csv/pdf (Phase 7) additionally excludes non-consenting
    residents and sensitive items — the JSON path above is unchanged
    pre-Phase-6 behavior. See services/exports.py."""
    if export_format != "json":
        rows = gated_top_items(db, days, limit)
        return _export_response(
            export_format, "top-items", "Top items", ["item_name", "count"], list(rows)
        )

    name = func.coalesce(Detection.corrected_item_name, Detection.item_name)
    query = (
        select(name.label("name"), func.count().label("count"))
        .join(Capture, Detection.capture_id == Capture.id)
        .where(
            Capture.captured_at >= _since(days),
            Detection.review_status != ReviewStatus.rejected,
            name != "unidentified_item",
        )
        .group_by(name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if bag_type is not None:
        query = query.where(Capture.bag_type == bag_type)
    return [ItemCount(name=n, count=c) for n, c in db.execute(query)]


@router.get("/analytics/top-brands", response_model=list[ItemCount])
def top_brands(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    export_format: str = Query("json", alias="format", pattern="^(json|csv|pdf)$"),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[ItemCount] | Response:
    """Most frequently matched brands from packaging OCR.

    format=csv/pdf (Phase 7): see top_items above."""
    if export_format != "json":
        rows = gated_top_brands(db, days, limit)
        return _export_response(
            export_format, "top-brands", "Top brands", ["brand", "count"], list(rows)
        )

    query = (
        select(Brand.name, func.count().label("count"))
        .join(Detection, Detection.matched_brand_id == Brand.id)
        .join(Capture, Detection.capture_id == Capture.id)
        .where(
            Capture.captured_at >= _since(days),
            Detection.review_status != ReviewStatus.rejected,
        )
        .group_by(Brand.name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [ItemCount(name=n, count=c) for n, c in db.execute(query)]


@router.get("/analytics/quality", response_model=QualityReport)
def quality_report(
    days: int = Query(30, ge=1, le=365),
    export_format: str = Query("json", alias="format", pattern="^(json|csv|pdf)$"),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> QualityReport | Response:
    """Model-health metrics: where is the vision model weak? High correction
    rates per item class point at what to fine-tune first.

    format=csv/pdf (Phase 7): see top_items above — the export's rows are
    gated even though this report is about model performance, not
    household behavior, per the phase's explicit instruction."""
    if export_format != "json":
        rows = gated_quality_rows(db, days)
        headers = ["item_name", "detections", "avg_confidence", "reviewed", "corrected"]
        return _export_response(export_format, "quality-report", "Quality report", headers, rows)

    since = _since(days)

    totals = db.execute(
        select(
            func.count(),
            func.coalesce(func.avg(Detection.confidence), 0.0),
            func.coalesce(func.avg(case((Detection.needs_review, 1.0), else_=0.0)), 0.0),
        )
        .join(Capture, Detection.capture_id == Capture.id)
        .where(Capture.captured_at >= since)
    ).one()

    captures_total, captures_failed = db.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(case((Capture.analysis_status == AnalysisStatus.failed, 1), else_=0)), 0
            ),
        ).where(Capture.captured_at >= since)
    ).one()

    by_item_rows = db.execute(
        select(
            Detection.item_name,
            func.count().label("detections"),
            func.avg(Detection.confidence).label("avg_confidence"),
            func.sum(case((Detection.review_status != ReviewStatus.unreviewed, 1), else_=0)).label(
                "reviewed"
            ),
            func.sum(case((Detection.review_status == ReviewStatus.corrected, 1), else_=0)).label(
                "corrected"
            ),
        )
        .join(Capture, Detection.capture_id == Capture.id)
        .where(Capture.captured_at >= since)
        .group_by(Detection.item_name)
        .order_by(func.count().desc())
        .limit(50)
    ).all()

    # Phase 5: accuracy per (prompt_version, model) — lets the new v2
    # paper/polythene contract be compared against v1 directly. Detections
    # that predate InferenceRun (inference_run_id NULL) or whose run never
    # recorded a prompt_version (the pre-Phase-5 gap this closes) fall into
    # a "None" group rather than being silently dropped.
    by_prompt_rows = db.execute(
        select(
            InferenceRun.prompt_version,
            InferenceRun.model_name,
            func.count().label("detections"),
            func.avg(Detection.confidence).label("avg_confidence"),
            func.sum(case((Detection.review_status != ReviewStatus.unreviewed, 1), else_=0)).label(
                "reviewed"
            ),
            func.sum(case((Detection.review_status == ReviewStatus.confirmed, 1), else_=0)).label(
                "confirmed"
            ),
            func.sum(case((Detection.review_status == ReviewStatus.corrected, 1), else_=0)).label(
                "corrected"
            ),
            func.sum(case((Detection.review_status == ReviewStatus.rejected, 1), else_=0)).label(
                "rejected"
            ),
        )
        .join(Capture, Detection.capture_id == Capture.id)
        .join(InferenceRun, Detection.inference_run_id == InferenceRun.id)
        .where(Capture.captured_at >= since)
        .group_by(InferenceRun.prompt_version, InferenceRun.model_name)
        .order_by(func.count().desc())
    ).all()

    return QualityReport(
        total_detections=totals[0],
        avg_confidence=round(float(totals[1]), 4),
        pct_needs_review=round(float(totals[2]) * 100, 2),
        capture_failure_rate=round(
            (captures_failed / captures_total * 100) if captures_total else 0.0, 2
        ),
        by_item=[
            QualityByItem(
                item_name=r.item_name,
                detections=r.detections,
                avg_confidence=round(float(r.avg_confidence), 4),
                reviewed=int(r.reviewed),
                corrected=int(r.corrected),
            )
            for r in by_item_rows
        ],
        by_prompt_version=[
            QualityByPromptVersion(
                prompt_version=r.prompt_version,
                model_name=r.model_name,
                detections=r.detections,
                avg_confidence=round(float(r.avg_confidence), 4),
                reviewed=int(r.reviewed),
                confirmed=int(r.confirmed),
                corrected=int(r.corrected),
                rejected=int(r.rejected),
                accuracy=round(int(r.confirmed) / int(r.reviewed), 4) if r.reviewed else 0.0,
            )
            for r in by_prompt_rows
        ],
    )


@router.get("/analytics/unmapped-brands", response_model=list[UnmappedBrandCount])
def unmapped_brands(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[UnmappedBrandCount]:
    """The "products we don't know about" report (Phase 5): brand_text read
    off packaging that never fuzzy-matched an existing Brand, most frequent
    first. This is how the Brand catalogue is meant to grow — a
    repeatedly-seen row here is a strong signal to add it via POST /brands."""
    rows = db.scalars(
        select(UnmappedLabel)
        .where(
            UnmappedLabel.label_kind == UnmappedLabelKind.BRAND,
            UnmappedLabel.resolved.is_(False),
        )
        .order_by(UnmappedLabel.occurrence_count.desc())
        .limit(limit)
    ).all()
    return [
        UnmappedBrandCount(
            raw_label=r.raw_label,
            bag_type=r.bag_type.value,
            occurrence_count=r.occurrence_count,
            first_seen_at=r.first_seen_at,
            last_seen_at=r.last_seen_at,
        )
        for r in rows
    ]


# --- Phase 6: household consumption layer -----------------------------
# Every read below goes through services/profiling.py, which enforces the
# consent/sensitivity/rejection gate in one place — nothing here
# re-implements or bypasses it.


@router.get("/profiles/{resident_id}/consumption", response_model=ConsumptionOut)
def consumption(
    resident_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> ConsumptionOut:
    """Replenishment cycles, brand loyalty, and packaged/fresh mix for one
    household. 404 both when the resident doesn't exist and when they
    don't currently consent to profiling — the two are indistinguishable
    on purpose, so this endpoint never reveals consent status."""
    result = get_consumption(db, resident_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return ConsumptionOut(
        resident_id=result["resident_id"],
        category_signals=[
            ConsumptionSignalOut.model_validate(s) for s in result["category_signals"]
        ],
        brand_signals=[ConsumptionSignalOut.model_validate(s) for s in result["brand_signals"]],
        brand_loyalty=result["brand_loyalty"],
        packaged_vs_fresh_ratio=result["packaged_vs_fresh_ratio"],
        spoiled_food_share=result["spoiled_food_share"],
    )


@router.get("/profiles/{resident_id}/predictions", response_model=list[ConsumptionSignalOut])
def predictions(
    resident_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[ConsumptionSignalOut]:
    """Subjects due (or overdue) for replenishment, soonest-due first. []
    for a nonexistent or non-consenting resident — matches GET
    /profiles/{id}'s existing empty-list-not-404 behavior for "nothing to
    show", since this endpoint's shape is a list, not a single object."""
    signals = get_predictions(db, resident_id)
    return [ConsumptionSignalOut.model_validate(s) for s in signals]


@router.get("/analytics/brand-switches", response_model=list[BrandSwitchEvent])
def brand_switches(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[BrandSwitchEvent]:
    """Brand A stops, brand B starts, same household and category, within
    the last `days`."""
    return [BrandSwitchEvent(**event) for event in detect_brand_switches(db, days)]


@router.get("/analytics/churn-risk", response_model=list[ChurnRiskItem])
def churn_risk(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[ChurnRiskItem]:
    """Categories a household used to dispose of regularly that have gone
    quiet for well beyond their own cycle — travel gaps (zero pickup
    requests in the window) are suppressed, not reported as churn."""
    return [ChurnRiskItem(**item) for item in detect_churn_risk(db)]


@router.get("/analytics/alerts", response_model=list[AlertOut])
def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[Alert]:
    """Failed-run-rate and daily-spend breaches (Phase 7), newest first —
    written by the hourly check-alerts job (services/alerting.py). No
    external notification channel exists in this project; this endpoint
    is the alert surface itself."""
    return list(db.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(limit)))
