"""Bag registration and QR lookup. Stations scan a bag's QR tag to find which
resident and bag type a tray belongs to."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import PII_ROLES, require_roles
from app.db import get_db
from app.models import Bag, Resident, StaffAccount, StaffRole
from app.schemas.captures import BagOut, BagRegister
from app.schemas.operations import BagWeighIn
from app.services.audit import record

router = APIRouter(prefix="/bags", tags=["bags"])

_WEIGH_ROLES = (StaffRole.collector, StaffRole.station_operator)


@router.post("", response_model=BagOut, status_code=status.HTTP_201_CREATED)
def register_bag(
    body: BagRegister,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*PII_ROLES)),
) -> Bag:
    if db.get(Resident, body.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resident not found")
    if db.scalar(select(Bag).where(Bag.tag_id == body.tag_id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tag already registered")
    bag = Bag(user_id=body.user_id, bag_type=body.bag_type, tag_id=body.tag_id)
    db.add(bag)
    db.flush()
    record(db, actor_id=account.id, action="bag.register", entity_type="bag", entity_id=str(bag.id))
    db.commit()
    db.refresh(bag)
    return bag


@router.get("/by-tag/{tag_id}", response_model=BagOut)
def lookup_by_tag(
    tag_id: str,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*PII_ROLES)),
) -> Bag:
    bag = db.scalar(select(Bag).where(Bag.tag_id == tag_id))
    if bag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tag")
    return bag


@router.get("/{bag_id}", response_model=BagOut)
def get_bag(
    bag_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*PII_ROLES)),
) -> Bag:
    """Phase 4: lets Station.tsx check whether a bag already has a weight
    before showing the weigh-in field."""
    bag = db.get(Bag, bag_id)
    if bag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bag not found")
    return bag


@router.patch("/{bag_id}/weigh", response_model=BagOut)
def weigh_bag(
    bag_id: uuid.UUID,
    body: BagWeighIn,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WEIGH_ROLES)),
) -> Bag:
    """Records gross/tare weight and condition — used both at the doorstep
    (nested into session creation) and later at the station if a bag still
    has no weight recorded (Phase 4 Station.tsx weigh-in field)."""
    bag = db.get(Bag, bag_id)
    if bag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bag not found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(bag, field, value)
    record(
        db,
        actor_id=account.id,
        action="bag.weigh",
        entity_type="bag",
        entity_id=str(bag_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(bag)
    return bag
