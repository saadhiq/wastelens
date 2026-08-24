"""Pickup booking: the demand side of a collection (a resident asking for a
pickup) vs. CollectionSession (the supply-side fulfillment). See
PickupRequest's docstring in models/operations.py."""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import PickupRequest, PickupStatus, Resident, StaffAccount, StaffRole
from app.schemas.common import Page
from app.schemas.operations import PickupCancel, PickupRequestCreate, PickupRequestOut
from app.services.audit import record

router = APIRouter(prefix="/pickups", tags=["pickups"])

_BOOKING_ROLES = (StaffRole.station_operator, StaffRole.collector)


@router.post("", response_model=PickupRequestOut, status_code=status.HTTP_201_CREATED)
def book_pickup(
    body: PickupRequestCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_BOOKING_ROLES)),
) -> PickupRequest:
    if db.get(Resident, body.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resident not found")
    pickup = PickupRequest(
        resident_id=body.user_id,
        requested_for_date=body.requested_for_date,
        requested_window=body.requested_window,
        channel=body.channel,
        declared_bag_count=body.declared_bag_count,
    )
    db.add(pickup)
    db.flush()
    record(
        db,
        actor_id=account.id,
        action="pickup.book",
        entity_type="pickup_request",
        entity_id=str(pickup.id),
    )
    db.commit()
    db.refresh(pickup)
    return pickup


@router.get("", response_model=Page[PickupRequestOut])
def list_pickups(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_BOOKING_ROLES, StaffRole.analyst)),
    date: dt.date | None = Query(None),
    pickup_status: PickupStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[PickupRequestOut]:
    query = select(PickupRequest)
    if date is not None:
        query = query.where(PickupRequest.requested_for_date == date)
    if pickup_status is not None:
        query = query.where(PickupRequest.status == pickup_status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(PickupRequest.requested_for_date.asc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[PickupRequestOut.model_validate(p) for p in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _get_pickup_or_404(db: Session, pickup_id: uuid.UUID) -> PickupRequest:
    pickup = db.get(PickupRequest, pickup_id)
    if pickup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pickup not found")
    return pickup


@router.patch("/{pickup_id}/cancel", response_model=PickupRequestOut)
def cancel_pickup(
    pickup_id: uuid.UUID,
    body: PickupCancel,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_BOOKING_ROLES)),
) -> PickupRequest:
    pickup = _get_pickup_or_404(db, pickup_id)
    if pickup.status not in (PickupStatus.REQUESTED,):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a pickup in status {pickup.status.value}",
        )
    pickup.status = PickupStatus.CANCELLED
    pickup.cancel_reason = body.reason
    record(
        db,
        actor_id=account.id,
        action="pickup.cancel",
        entity_type="pickup_request",
        entity_id=str(pickup_id),
        detail={"reason": body.reason or ""},
    )
    db.commit()
    db.refresh(pickup)
    return pickup


@router.post("/{pickup_id}/miss", response_model=PickupRequestOut)
def miss_pickup(
    pickup_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_BOOKING_ROLES)),
) -> PickupRequest:
    """The collector reached (or attempted) the route slot and nobody handed
    over waste — distinct from a resident-initiated cancel."""
    pickup = _get_pickup_or_404(db, pickup_id)
    if pickup.status != PickupStatus.REQUESTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot mark missed a pickup in status {pickup.status.value}",
        )
    pickup.status = PickupStatus.MISSED
    record(
        db,
        actor_id=account.id,
        action="pickup.miss",
        entity_type="pickup_request",
        entity_id=str(pickup_id),
    )
    db.commit()
    db.refresh(pickup)
    return pickup
