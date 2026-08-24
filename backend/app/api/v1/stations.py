"""Inspection station CRUD — the catalogued capture stations a Capture can
optionally point at via inspection_station_id (see waste.py / DECISIONS.md
#16). Admin only: stations are physical-facility configuration, not
day-to-day operator data."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import InspectionStation, StaffAccount, StaffRole
from app.schemas.common import Page
from app.schemas.operations import StationCreate, StationOut, StationUpdate
from app.services.audit import record

router = APIRouter(prefix="/stations", tags=["stations"])


@router.post("", response_model=StationOut, status_code=status.HTTP_201_CREATED)
def create_station(
    body: StationCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> InspectionStation:
    if db.scalar(
        select(InspectionStation).where(InspectionStation.station_code == body.station_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Station code already exists"
        )
    station = InspectionStation(**body.model_dump())
    db.add(station)
    db.flush()
    record(
        db,
        actor_id=account.id,
        action="station.create",
        entity_type="station",
        entity_id=str(station.id),
    )
    db.commit()
    db.refresh(station)
    return station


@router.get("", response_model=Page[StationOut])
def list_stations(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[StationOut]:
    total = db.scalar(select(func.count()).select_from(InspectionStation)) or 0
    rows = db.scalars(
        select(InspectionStation)
        .order_by(InspectionStation.station_code)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[StationOut.model_validate(s) for s in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{station_id}", response_model=StationOut)
def get_station(
    station_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> InspectionStation:
    station = db.get(InspectionStation, station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
    return station


@router.patch("/{station_id}", response_model=StationOut)
def update_station(
    station_id: uuid.UUID,
    body: StationUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> InspectionStation:
    station = db.get(InspectionStation, station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(station, field, value)
    record(
        db,
        actor_id=account.id,
        action="station.update",
        entity_type="station",
        entity_id=str(station_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(station)
    return station


@router.delete("/{station_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_station(
    station_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> None:
    station = db.get(InspectionStation, station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
    db.delete(station)
    record(
        db,
        actor_id=account.id,
        action="station.delete",
        entity_type="station",
        entity_id=str(station_id),
    )
    db.commit()
