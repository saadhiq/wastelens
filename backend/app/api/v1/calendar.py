"""Calendar reference data. Rows are seeded (app/seeds/seed.py:
seed_calendar_days) for the current and next year with is_poya /
is_public_holiday defaulted to False — deliberately not hardcoded, since a
wrong Poya calendar would corrupt every seasonality feature built on top of
it later (see DECISIONS.md). Editing those two flags (and note) is admin
only; reading is open to any authenticated role since analytics/collector
scheduling both need it."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_account, require_roles
from app.db import get_db
from app.models import CalendarDay, StaffAccount, StaffRole
from app.schemas.common import Page
from app.schemas.operations import CalendarDayOut, CalendarDayUpdate
from app.services.audit import record

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", response_model=Page[CalendarDayOut])
def list_calendar_days(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(get_current_account),
    year: int = Query(..., ge=2000, le=2100),
    limit: int = Query(400, ge=1, le=400),
    offset: int = Query(0, ge=0),
) -> Page[CalendarDayOut]:
    query = select(CalendarDay).where(
        CalendarDay.calendar_date >= dt.date(year, 1, 1),
        CalendarDay.calendar_date <= dt.date(year, 12, 31),
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(CalendarDay.calendar_date).limit(limit).offset(offset)).all()
    return Page(
        items=[CalendarDayOut.model_validate(d) for d in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{calendar_date}", response_model=CalendarDayOut)
def update_calendar_day(
    calendar_date: dt.date,
    body: CalendarDayUpdate,
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> CalendarDay:
    day = db.get(CalendarDay, calendar_date)
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar day not seeded")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(day, field, value)
    record(
        db,
        actor_id=account.id,
        action="calendar.update",
        entity_type="calendar_day",
        entity_id=calendar_date.isoformat(),
        detail={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(day)
    return day
