"""Bin CRUD (admin only) + POST /bins/transfer — the paper trail for a
bag's physical hand-off from a tray into a downstream bin after inspection.
Transfer is open to station_operator/collector as well as admin since it's
a routine facility action, not configuration."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import Bag, Bin, BinTransfer, StaffAccount, StaffRole
from app.schemas.common import Page
from app.schemas.operations import BinCreate, BinOut, BinTransferCreate, BinTransferOut, BinUpdate
from app.services.audit import record

router = APIRouter(prefix="/bins", tags=["bins"])

_TRANSFER_ROLES = (StaffRole.station_operator, StaffRole.collector)


@router.post("", response_model=BinOut, status_code=status.HTTP_201_CREATED)
def create_bin(
    body: BinCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> Bin:
    if db.scalar(select(Bin).where(Bin.bin_code == body.bin_code)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bin code already exists")
    bin_ = Bin(**body.model_dump())
    db.add(bin_)
    db.flush()
    record(db, actor_id=account.id, action="bin.create", entity_type="bin", entity_id=str(bin_.id))
    db.commit()
    db.refresh(bin_)
    return bin_


@router.get("", response_model=Page[BinOut])
def list_bins(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin, *_TRANSFER_ROLES)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[BinOut]:
    total = db.scalar(select(func.count()).select_from(Bin)) or 0
    rows = db.scalars(select(Bin).order_by(Bin.bin_code).limit(limit).offset(offset)).all()
    return Page(
        items=[BinOut.model_validate(b) for b in rows], total=total, limit=limit, offset=offset
    )


@router.patch("/{bin_id}", response_model=BinOut)
def update_bin(
    bin_id: uuid.UUID,
    body: BinUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> Bin:
    bin_ = db.get(Bin, bin_id)
    if bin_ is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bin not found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(bin_, field, value)
    record(
        db,
        actor_id=account.id,
        action="bin.update",
        entity_type="bin",
        entity_id=str(bin_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(bin_)
    return bin_


@router.delete("/{bin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bin(
    bin_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> None:
    bin_ = db.get(Bin, bin_id)
    if bin_ is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bin not found")
    db.delete(bin_)
    record(db, actor_id=account.id, action="bin.delete", entity_type="bin", entity_id=str(bin_id))
    db.commit()


@router.post("/transfer", response_model=BinTransferOut, status_code=status.HTTP_201_CREATED)
def transfer_bag(
    body: BinTransferCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_TRANSFER_ROLES)),
) -> BinTransfer:
    bag = db.get(Bag, body.bag_id)
    if bag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bag not found")
    bin_ = db.get(Bin, body.bin_id)
    if bin_ is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bin not found")

    transfer = BinTransfer(
        bag_id=body.bag_id,
        bin_id=body.bin_id,
        from_tray_code=body.from_tray_code,
        operator_id=account.id,
        weight_kg=body.weight_kg,
    )
    bag.assigned_bin_id = body.bin_id
    db.add(transfer)
    db.flush()
    record(
        db,
        actor_id=account.id,
        action="bin.transfer",
        entity_type="bin_transfer",
        entity_id=str(transfer.id),
        detail={"bag_id": str(body.bag_id), "bin_id": str(body.bin_id)},
    )
    db.commit()
    db.refresh(transfer)
    return transfer
