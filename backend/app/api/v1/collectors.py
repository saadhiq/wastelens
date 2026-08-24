"""Collector (field-staff) CRUD, admin only. Not in the Phase 4 spec's
explicit endpoint list, but required by its own "Admin screens for
stations, bins, collectors, calendar" line — there's no other way to create
the Collector row a StaffAccount needs before it can drive a route."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import Collector, StaffAccount, StaffRole
from app.schemas.common import Page
from app.schemas.operations import CollectorCreate, CollectorOut, CollectorUpdate
from app.services.audit import record

router = APIRouter(prefix="/collectors", tags=["collectors"])


@router.post("", response_model=CollectorOut, status_code=status.HTTP_201_CREATED)
def create_collector(
    body: CollectorCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> Collector:
    staff = db.get(StaffAccount, body.staff_account_id)
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff account not found")
    if staff.role != StaffRole.collector:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Staff account must have the collector role",
        )
    if db.scalar(select(Collector).where(Collector.staff_account_id == body.staff_account_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Staff account already has a collector profile",
        )
    if db.scalar(select(Collector).where(Collector.employee_code == body.employee_code)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Employee code already exists"
        )
    collector = Collector(**body.model_dump())
    db.add(collector)
    db.flush()
    record(
        db,
        actor_id=account.id,
        action="collector.create",
        entity_type="collector",
        entity_id=str(collector.id),
    )
    db.commit()
    db.refresh(collector)
    return collector


@router.get("", response_model=Page[CollectorOut])
def list_collectors(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[CollectorOut]:
    total = db.scalar(select(func.count()).select_from(Collector)) or 0
    rows = db.scalars(
        select(Collector).order_by(Collector.full_name).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[CollectorOut.model_validate(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{collector_id}", response_model=CollectorOut)
def update_collector(
    collector_id: uuid.UUID,
    body: CollectorUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> Collector:
    collector = db.get(Collector, collector_id)
    if collector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collector not found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(collector, field, value)
    record(
        db,
        actor_id=account.id,
        action="collector.update",
        entity_type="collector",
        entity_id=str(collector_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(collector)
    return collector
