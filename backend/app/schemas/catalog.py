"""Schemas for the living taxonomies (Phase 3 admin CRUD): item vocabulary,
brands, and the unmapped-label queue that feeds new vocabulary entries."""

import datetime as dt
import uuid

from pydantic import BaseModel

from app.models.base import BagType, UnmappedLabelKind


class VocabularyItemCreate(BaseModel):
    bag_type: BagType
    item_name: str
    display_name: str
    parent_category: str | None = None
    parent_id: uuid.UUID | None = None
    active: bool = True
    is_contaminant_by_default: bool = False
    is_sensitive: bool = False


class VocabularyItemUpdate(BaseModel):
    """All optional — PATCH semantics, only provided fields change."""

    display_name: str | None = None
    parent_category: str | None = None
    parent_id: uuid.UUID | None = None
    active: bool | None = None
    is_contaminant_by_default: bool | None = None
    is_sensitive: bool | None = None


class VocabularyItemOut(BaseModel):
    id: uuid.UUID
    bag_type: BagType
    item_name: str
    display_name: str
    parent_category: str | None
    parent_id: uuid.UUID | None
    active: bool
    is_contaminant_by_default: bool
    is_sensitive: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class BrandCreate(BaseModel):
    name: str
    aliases: list[str] = []
    category: str | None = None


class BrandUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    category: str | None = None


class BrandOut(BaseModel):
    id: uuid.UUID
    name: str
    aliases: list[str]
    category: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class UnmappedLabelOut(BaseModel):
    id: uuid.UUID
    raw_label: str
    bag_type: BagType
    label_kind: UnmappedLabelKind
    occurrence_count: int
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    suggested_vocabulary_item_id: uuid.UUID | None
    resolved: bool

    model_config = {"from_attributes": True}


class PromoteUnmappedLabel(BaseModel):
    """Body for POST /vocabulary/from-unmapped/{id}. item_name/display_name
    default to the raw label if not overridden — the common case is
    promoting it as-is."""

    item_name: str | None = None
    display_name: str | None = None
    parent_category: str | None = None
    parent_id: uuid.UUID | None = None
