"""Review workflow endpoints (Phase 3): confirm/correct/reject a detection,
bulk-confirm, the review queue, and reviewer stats. Role: reviewer + admin.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import Detection, ReviewStatus, StaffAccount, StaffRole
from app.schemas.captures import DetectionOut
from app.schemas.common import Page
from app.schemas.review import (
    BulkReviewRequest,
    BulkReviewResult,
    ReviewAction,
    ReviewQueueItem,
    ReviewStats,
)
from app.services.review import (
    apply_review,
    build_review_queue,
    compute_review_stats,
    count_review_queue,
)

router = APIRouter(tags=["review"])

_REVIEW_ROLES = (StaffRole.reviewer,)


@router.post("/detections/{detection_id}/review", response_model=DetectionOut)
def review_detection(
    detection_id: uuid.UUID,
    body: ReviewAction,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_REVIEW_ROLES)),
) -> Detection:
    detection = db.get(Detection, detection_id)
    if detection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Detection not found")
    apply_review(db, detection=detection, reviewer=account, action=body)
    return detection


@router.post("/detections/bulk-review", response_model=BulkReviewResult)
def bulk_review_detections(
    body: BulkReviewRequest,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_REVIEW_ROLES)),
) -> BulkReviewResult:
    reviewed = 0
    skipped: list[uuid.UUID] = []
    action = ReviewAction(verdict=body.verdict)
    for detection_id in body.detection_ids:
        detection = db.get(Detection, detection_id)
        if detection is None or detection.review_status != ReviewStatus.unreviewed:
            skipped.append(detection_id)
            continue
        apply_review(db, detection=detection, reviewer=account, action=action)
        reviewed += 1
    return BulkReviewResult(reviewed=reviewed, skipped=skipped)


@router.get("/review/queue", response_model=Page[ReviewQueueItem])
def get_review_queue(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_REVIEW_ROLES)),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[ReviewQueueItem]:
    total = count_review_queue(db)
    rows = build_review_queue(db, limit=limit, offset=offset)
    items = [
        ReviewQueueItem(
            **DetectionOut.model_validate(row["detection"]).model_dump(),
            capture_id=row["capture_id"],
            capture_image_url=row["capture_image_url"],
            capture_bag_type=row["capture_bag_type"],
            captured_at=row["captured_at"],
            queue_reason=row["queue_reason"],
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/review/stats", response_model=ReviewStats)
def get_review_stats(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_REVIEW_ROLES)),
) -> ReviewStats:
    return compute_review_stats(db)
