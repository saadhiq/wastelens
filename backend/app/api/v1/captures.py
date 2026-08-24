"""Capture endpoints: the station uploads one tray photo per emptied bag.

POST /captures stores the image in object storage, creates the DB row, and
enqueues the async analysis job — it returns immediately with the capture id.
An Idempotency-Key header makes retries safe on flaky station connections.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.logging import get_logger
from app.db import get_db
from app.models import (
    AnalysisStatus,
    Bag,
    BagStatus,
    Capture,
    CollectionSession,
    StaffAccount,
    StaffRole,
)
from app.schemas.captures import CaptureDetail, CaptureOut, SessionCreate, SessionOut
from app.schemas.common import Page
from app.services import storage
from app.services.audit import record

log = get_logger(__name__)

router = APIRouter(tags=["captures"])

_CAPTURE_ROLES = (StaffRole.station_operator,)


def enqueue_analysis(capture_id: uuid.UUID) -> None:
    """Push the analysis job to the worker. Isolated so tests can run it inline."""
    from app.worker import analyze_capture_task

    analyze_capture_task.delay(str(capture_id))


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    body: SessionCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_CAPTURE_ROLES)),
) -> CollectionSession:
    session = CollectionSession(user_id=body.user_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.post("/captures", response_model=CaptureOut, status_code=status.HTTP_201_CREATED)
def create_capture(
    image: UploadFile = File(...),
    bag_tag_id: str = Form(...),
    station_id: str = Form(...),
    session_id: uuid.UUID | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_CAPTURE_ROLES)),
) -> Capture:
    # Idempotent retry: same key → return the existing capture, no new job.
    if idempotency_key:
        existing = db.scalar(select(Capture).where(Capture.idempotency_key == idempotency_key))
        if existing is not None:
            return existing

    media_type = image.content_type or ""
    if media_type not in storage.ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type {media_type!r}; use one of "
            f"{sorted(storage.ALLOWED_MEDIA_TYPES)}",
        )

    bag = db.scalar(select(Bag).where(Bag.tag_id == bag_tag_id))
    if bag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown bag tag")

    # Auto-create a session when the station didn't send one (see DECISIONS.md).
    if session_id is not None:
        session = db.get(CollectionSession, session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown session")
    else:
        session = CollectionSession(user_id=bag.user_id)
        db.add(session)
        db.flush()

    capture_id = uuid.uuid4()
    key = storage.object_key(str(capture_id), media_type)
    data = image.file.read()
    storage.upload_image(key, data, media_type)

    capture = Capture(
        id=capture_id,
        session_id=session.id,
        bag_id=bag.id,
        bag_type=bag.bag_type,
        image_url=key,
        station_id=station_id,
        operator_id=account.id,
        analysis_status=AnalysisStatus.pending,
        idempotency_key=idempotency_key,
    )
    bag.status = BagStatus.collected
    db.add(capture)
    record(
        db,
        actor_id=account.id,
        action="capture.create",
        entity_type="capture",
        entity_id=str(capture_id),
        detail={"station_id": station_id, "bag_type": bag.bag_type.value},
    )
    db.commit()
    db.refresh(capture)

    enqueue_analysis(capture.id)
    log.info("capture_enqueued", capture_id=str(capture.id), station_id=station_id)
    return capture


@router.get("/captures", response_model=Page[CaptureOut])
def list_captures(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(
        require_roles(StaffRole.station_operator, StaffRole.reviewer, StaffRole.analyst)
    ),
    analysis_status: AnalysisStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[CaptureOut]:
    query = select(Capture)
    if analysis_status is not None:
        query = query.where(Capture.analysis_status == analysis_status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Capture.captured_at.desc()).limit(limit).offset(offset)).all()
    return Page(
        items=[CaptureOut.model_validate(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/captures/{capture_id}", response_model=CaptureDetail)
def get_capture(
    capture_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(
        require_roles(StaffRole.station_operator, StaffRole.reviewer, StaffRole.analyst)
    ),
) -> CaptureDetail:
    capture = db.get(Capture, capture_id)
    if capture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture not found")
    # capture.image_url is an S3 key; the response needs a URL the browser
    # can actually load the image from (see storage.presigned_get_url).
    out = CaptureDetail.model_validate(capture)
    out.image_url = storage.presigned_get_url(capture.image_url)
    return out
