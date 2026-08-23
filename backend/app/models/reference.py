"""Static calendar reference data — makes collection-schedule and analytics
queries calendar-aware (weekday patterns, poya days, public holidays)
without hardcoding date logic in application code. New in Phase 1's domain
extension.
"""

import datetime as dt

from sqlalchemy import Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CalendarDay(Base):
    """One row per calendar date. Populated by a future seed/backfill job —
    nothing in Phase 1 writes to this table."""

    __tablename__ = "calendar_days"

    calendar_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=Monday .. 6=Sunday, ISO-style
    is_weekend: Mapped[bool] = mapped_column(Boolean, default=False)
    is_poya: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public_holiday: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
