"""Materialized weekly waste profiles per resident, rebuilt by a scheduled
aggregation job (Phase 3). One row per (user, ISO week)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserWasteProfile(Base):
    __tablename__ = "user_waste_profiles"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_profile_user_week"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    week_start: Mapped[dt.date] = mapped_column(Date)  # Monday of the ISO week
    veg_frequency: Mapped[int] = mapped_column(Integer, default=0)
    top_vegetables: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    packaged_food_frequency: Mapped[int] = mapped_column(Integer, default=0)
    top_brands: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    category_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rebuilt_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
