"""FastAPI dependencies: current-user resolution and role-based access control.

Usage:
    @router.get("/thing", dependencies=[Depends(require_roles(StaffRole.admin))])
or inject the account:
    account: StaffAccount = Depends(require_roles(StaffRole.admin, StaffRole.analyst))
"""

import uuid

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db import get_db
from app.models import StaffAccount, StaffRole

_bearer = HTTPBearer(auto_error=False)

# Roles allowed to read resident PII (name, phone, address).
PII_ROLES = (StaffRole.admin, StaffRole.station_operator)


def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> StaffAccount:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        account_id = uuid.UUID(payload["sub"])
    except (pyjwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    account = db.get(StaffAccount, account_id)
    if account is None or not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or disabled"
        )
    return account


def require_roles(*roles: StaffRole):
    """Dependency factory: 403 unless the caller's role is in `roles`.
    Admin is always allowed."""

    allowed = set(roles) | {StaffRole.admin}

    def checker(account: StaffAccount = Depends(get_current_account)) -> StaffAccount:
        if account.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {sorted(r.value for r in allowed)}",
            )
        return account

    return checker
