"""Vocabulary CRUD (Phase 3): the item_name/display_name catalog the CV
prompts are built from, plus promoting an UnmappedLabel into a real entry.

Reads (list/get, including the unmapped-label queue) are open to any
authenticated role — the review page's correction autocomplete and
unmapped-label inbox both need this. Writes (create/update/delete/promote)
are admin only, per spec.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_account, require_roles
from app.db import get_db
from app.models import (
    BagType,
    StaffAccount,
    StaffRole,
    UnmappedLabel,
    UnmappedLabelKind,
    VocabularyItem,
)
from app.schemas.catalog import (
    PromoteUnmappedLabel,
    UnmappedLabelOut,
    VocabularyItemCreate,
    VocabularyItemOut,
    VocabularyItemUpdate,
)
from app.schemas.common import Page
from app.services.audit import record

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])

_WRITE_ROLES = (StaffRole.admin,)


# --- Unmapped-label queue (literal paths registered before /{id} below) ---


@router.get("/unmapped", response_model=Page[UnmappedLabelOut])
def list_unmapped_labels(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
    resolved: bool = Query(False),
    # Defaults to ITEM so this endpoint's existing consumer (the review
    # page's vocabulary inbox) sees exactly what it always has — Phase 5's
    # BRAND rows surface separately, via GET /analytics/unmapped-brands.
    label_kind: UnmappedLabelKind = Query(UnmappedLabelKind.ITEM),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[UnmappedLabelOut]:
    query = select(UnmappedLabel).where(
        UnmappedLabel.resolved.is_(resolved), UnmappedLabel.label_kind == label_kind
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(UnmappedLabel.occurrence_count.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[UnmappedLabelOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/from-unmapped/{unmapped_id}",
    response_model=VocabularyItemOut,
    status_code=status.HTTP_201_CREATED,
)
def promote_unmapped_label(
    unmapped_id: uuid.UUID,
    body: PromoteUnmappedLabel,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> VocabularyItem:
    unmapped = db.get(UnmappedLabel, unmapped_id)
    if unmapped is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unmapped label not found"
        )
    if unmapped.resolved:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already resolved")
    if unmapped.label_kind != UnmappedLabelKind.ITEM:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only ITEM-kind unmapped labels can be promoted into vocabulary",
        )

    item_name = body.item_name or unmapped.raw_label
    existing = db.scalar(
        select(VocabularyItem).where(
            VocabularyItem.bag_type == unmapped.bag_type, VocabularyItem.item_name == item_name
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{item_name!r} already exists for bag_type={unmapped.bag_type.value}",
        )

    item = VocabularyItem(
        bag_type=unmapped.bag_type,
        item_name=item_name,
        display_name=body.display_name or item_name.replace("_", " ").title(),
        parent_category=body.parent_category,
        parent_id=body.parent_id,
    )
    db.add(item)
    db.flush()

    unmapped.resolved = True
    unmapped.suggested_vocabulary_item_id = item.id

    record(
        db,
        actor_id=account.id,
        action="vocabulary.promote_from_unmapped",
        entity_type="vocabulary_item",
        entity_id=str(item.id),
        detail={"unmapped_label_id": str(unmapped.id), "raw_label": unmapped.raw_label},
    )
    db.commit()
    db.refresh(item)
    return item


# --- Vocabulary CRUD ---


@router.get("", response_model=Page[VocabularyItemOut])
def list_vocabulary(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
    bag_type: BagType | None = Query(None),
    active: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[VocabularyItemOut]:
    query = select(VocabularyItem)
    if bag_type is not None:
        query = query.where(VocabularyItem.bag_type == bag_type)
    if active is not None:
        query = query.where(VocabularyItem.active.is_(active))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    query = query.order_by(VocabularyItem.bag_type, VocabularyItem.item_name)
    rows = db.scalars(query.limit(limit).offset(offset)).all()
    return Page(
        items=[VocabularyItemOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=VocabularyItemOut, status_code=status.HTTP_201_CREATED)
def create_vocabulary_item(
    body: VocabularyItemCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> VocabularyItem:
    existing = db.scalar(
        select(VocabularyItem).where(
            VocabularyItem.bag_type == body.bag_type, VocabularyItem.item_name == body.item_name
        )
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already exists")
    item = VocabularyItem(**body.model_dump())
    db.add(item)
    record(
        db,
        actor_id=account.id,
        action="vocabulary.create",
        entity_type="vocabulary_item",
        detail={"bag_type": body.bag_type.value, "item_name": body.item_name},
    )
    db.commit()
    db.refresh(item)
    return item


@router.get("/{item_id}", response_model=VocabularyItemOut)
def get_vocabulary_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
) -> VocabularyItem:
    item = db.get(VocabularyItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return item


@router.patch("/{item_id}", response_model=VocabularyItemOut)
def update_vocabulary_item(
    item_id: uuid.UUID,
    body: VocabularyItemUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> VocabularyItem:
    item = db.get(VocabularyItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)
    record(
        db,
        actor_id=account.id,
        action="vocabulary.update",
        entity_type="vocabulary_item",
        entity_id=str(item_id),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vocabulary_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_WRITE_ROLES)),
) -> None:
    item = db.get(VocabularyItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(item)
    record(
        db,
        actor_id=account.id,
        action="vocabulary.delete",
        entity_type="vocabulary_item",
        entity_id=str(item_id),
    )
    db.commit()
