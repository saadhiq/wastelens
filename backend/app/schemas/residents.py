"""Resident (household) schemas. Two output shapes exist deliberately:

- ResidentOut       — includes PII (name, phone, address); only returned on
                      role-gated endpoints, and each read is audit-logged.
- ResidentAnonymous — user_id only; safe for analytics and exports.
"""

import datetime as dt
import re
import uuid

from pydantic import BaseModel, field_validator

_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


class ResidentCreate(BaseModel):
    name: str
    phone: str
    address: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = v.replace(" ", "").replace("-", "")
        if not _PHONE_RE.match(v):
            raise ValueError("phone must be 7-15 digits, optionally prefixed with +")
        return v


class ResidentUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.replace(" ", "").replace("-", "")
        if not _PHONE_RE.match(v):
            raise ValueError("phone must be 7-15 digits, optionally prefixed with +")
        return v


class ResidentOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str
    address: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ResidentAnonymous(BaseModel):
    id: uuid.UUID
    created_at: dt.datetime

    model_config = {"from_attributes": True}
