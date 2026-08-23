"""Auth endpoints: login, token refresh, and admin-only staff account creation."""

import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_account, require_roles
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db import get_db
from app.models import StaffAccount, StaffRole
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    StaffAccountCreate,
    StaffAccountOut,
    TokenPair,
)
from app.services.audit import record

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    account = db.scalar(select(StaffAccount).where(StaffAccount.email == body.email))
    if account is None or not verify_password(body.password, account.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not account.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    record(db, actor_id=account.id, action="auth.login")
    db.commit()
    subject = str(account.id)
    return TokenPair(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
        account_id = uuid.UUID(payload["sub"])
    except (pyjwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from exc
    account = db.get(StaffAccount, account_id)
    if account is None or not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or disabled"
        )
    subject = str(account.id)
    return TokenPair(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


@router.get("/me", response_model=StaffAccountOut)
def me(account: StaffAccount = Depends(get_current_account)) -> StaffAccount:
    return account


@router.post(
    "/staff",
    response_model=StaffAccountOut,
    status_code=status.HTTP_201_CREATED,
)
def create_staff_account(
    body: StaffAccountCreate,
    db: Session = Depends(get_db),
    admin: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> StaffAccount:
    existing = db.scalar(select(StaffAccount).where(StaffAccount.email == body.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    account = StaffAccount(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(account)
    record(
        db,
        actor_id=admin.id,
        action="staff.create",
        entity_type="staff_account",
        detail={"email": body.email, "role": body.role.value},
    )
    db.commit()
    db.refresh(account)
    return account
