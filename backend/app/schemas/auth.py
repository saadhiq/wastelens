"""Auth request/response schemas."""

import uuid

from pydantic import BaseModel, EmailStr

from app.models.base import StaffRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class StaffAccountOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: StaffRole
    is_active: bool

    model_config = {"from_attributes": True}


class StaffAccountCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: StaffRole
