"""Residents (households) CRUD. PII reads are role-gated to admin +
station_operator and audit-logged; analysts get the anonymized shape only."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PII_ROLES, get_current_account, require_roles
from app.db import get_db
from app.models import Resident, StaffAccount, StaffRole
from app.schemas.common import Page
from app.schemas.residents import (
    ResidentAnonymous,
    ResidentCreate,
    ResidentOut,
    ResidentUpdate,
)
from app.services.audit import record

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=ResidentOut, status_code=status.HTTP_201_CREATED)
def create_resident(
    body: ResidentCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*PII_ROLES)),
) -> Resident:
    existing = db.scalar(select(Resident).where(Resident.phone == body.phone))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone already registered")
    resident = Resident(name=body.name, phone=body.phone, address=body.address)
    db.add(resident)
    db.flush()
    record(
        db,
        actor_id=account.id,
        action="user.create",
        entity_type="user",
        entity_id=str(resident.id),
    )
    db.commit()
    db.refresh(resident)
    return resident


@router.get("", response_model=Page[ResidentAnonymous])
def list_residents(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[ResidentAnonymous]:
    """Anonymized listing (no PII) — available to any authenticated role."""
    total = db.scalar(select(func.count()).select_from(Resident)) or 0
    rows = db.scalars(
        select(Resident).order_by(Resident.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[ResidentAnonymous.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{user_id}", response_model=ResidentOut)
def get_resident(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*PII_ROLES)),
) -> Resident:
    """Full record including PII. Role-gated; every read is audit-logged."""
    resident = db.get(Resident, user_id)
    if resident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resident not found")
    record(
        db,
        actor_id=account.id,
        action="pii.read",
        entity_type="user",
        entity_id=str(user_id),
    )
    db.commit()
    return resident


@router.patch("/{user_id}", response_model=ResidentOut)
def update_resident(
    user_id: uuid.UUID,
    body: ResidentUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*PII_ROLES)),
) -> Resident:
    resident = db.get(Resident, user_id)
    if resident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resident not found")
    changes = body.model_dump(exclude_unset=True)
    if "phone" in changes:
        dup = db.scalar(
            select(Resident).where(Resident.phone == changes["phone"], Resident.id != user_id)
        )
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Phone already registered"
            )
    for field, value in changes.items():
        setattr(resident, field, value)
    record(
        db,
        actor_id=account.id,
        action="user.update",
        entity_type="user",
        entity_id=str(user_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(resident)
    return resident


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resident(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> None:
    resident = db.get(Resident, user_id)
    if resident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resident not found")
    db.delete(resident)
    record(
        db,
        actor_id=account.id,
        action="user.delete",
        entity_type="user",
        entity_id=str(user_id),
    )
    db.commit()
