"""Brand CRUD (Phase 3): the name/alias list OCR text is fuzzy-matched
against. Reads open to any authenticated role; writes admin only."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_account, require_roles
from app.db import get_db
from app.models import Brand, StaffAccount, StaffRole
from app.schemas.catalog import BrandCreate, BrandOut, BrandUpdate
from app.schemas.common import Page
from app.services.audit import record

router = APIRouter(prefix="/brands", tags=["brands"])

_WRITE_ROLES = (StaffRole.admin,)


@router.get("", response_model=Page[BrandOut])
def list_brands(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[BrandOut]:
    query = select(Brand)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Brand.name).limit(limit).offset(offset)).all()
    return Page(
        items=[BrandOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
def create_brand(
    body: BrandCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> Brand:
    if db.scalar(select(Brand).where(Brand.name == body.name)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already exists")
    brand = Brand(**body.model_dump())
    db.add(brand)
    record(
        db,
        actor_id=account.id,
        action="brand.create",
        entity_type="brand",
        detail={"name": body.name},
    )
    db.commit()
    db.refresh(brand)
    return brand


@router.get("/{brand_id}", response_model=BrandOut)
def get_brand(
    brand_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
) -> Brand:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return brand


@router.patch("/{brand_id}", response_model=BrandOut)
def update_brand(
    brand_id: uuid.UUID,
    body: BrandUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> Brand:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        dup = db.scalar(select(Brand).where(Brand.name == changes["name"], Brand.id != brand_id))
        if dup is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Name already in use")
    for field, value in changes.items():
        setattr(brand, field, value)
    record(
        db,
        actor_id=account.id,
        action="brand.update",
        entity_type="brand",
        entity_id=str(brand_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(brand)
    return brand


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand(
    brand_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> None:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(brand)
    record(
        db,
        actor_id=account.id,
        action="brand.delete",
        entity_type="brand",
        entity_id=str(brand_id),
    )
    db.commit()
