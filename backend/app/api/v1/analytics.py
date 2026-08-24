"""Waste profiles and facility analytics (analyst/admin only, anonymized —
these endpoints expose user_id but never name/phone/address)."""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import (
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

router = APIRouter(tags=["analytics"])

_ANALYTICS_ROLES = (StaffRole.analyst,)


def _since(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


@router.get("/profiles/{user_id}", response_model=list[ProfileOut])
def get_user_profiles(
    user_id: uuid.UUID,
    weeks: int = Query(12, ge=1, le=104),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[UserWasteProfile]:
    """A household's weekly waste-profile timeline, newest first."""
    return list(
        db.scalars(
            select(UserWasteProfile)
            .where(UserWasteProfile.user_id == user_id)
            .order_by(UserWasteProfile.week_start.desc())
            .limit(weeks)
        )
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
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[ItemCount]:
    """Most frequent (trustworthy) items across all households."""
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
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> list[ItemCount]:
    """Most frequently matched brands from packaging OCR."""
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
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_ANALYTICS_ROLES)),
) -> QualityReport:
    """Model-health metrics: where is the vision model weak? High correction
    rates per item class point at what to fine-tune first."""
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
