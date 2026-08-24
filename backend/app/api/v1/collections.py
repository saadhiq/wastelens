"""Collection session endpoints: the collector's doorstep flow (Phase 4).

POST /sessions was previously a one-line "just give me a user_id" call in
captures.py; it now accepts collector/vehicle/route/GPS and every bag
collected in one nested request — the substantive logic lives in
services/collections.py. Kept at the router-tag level captures.py used
(no path prefix), so the route stays /sessions, not /collections/sessions.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import CollectionSession, StaffAccount, StaffRole
from app.schemas.captures import SessionArrive, SessionCreate, SessionDetail, SessionOut
from app.services.collections import create_session_with_bags, mark_session_arrived

router = APIRouter(tags=["collections"])

_COLLECTOR_ROLES = (StaffRole.collector, StaffRole.station_operator)


@router.post("/sessions", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
def create_session(
    body: SessionCreate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_COLLECTOR_ROLES)),
) -> CollectionSession:
    return create_session_with_bags(db, body=body, account=account)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(
        require_roles(StaffRole.collector, StaffRole.station_operator, StaffRole.analyst)
    ),
) -> CollectionSession:
    session = db.get(CollectionSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.patch("/sessions/{session_id}/arrive", response_model=SessionOut)
def arrive_session(
    session_id: uuid.UUID,
    body: SessionArrive,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(*_COLLECTOR_ROLES)),
) -> CollectionSession:
    session = db.get(CollectionSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return mark_session_arrived(db, session=session, arrived_at=body.arrived_at, account=account)
