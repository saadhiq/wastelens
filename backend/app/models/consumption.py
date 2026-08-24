"""Household consumption signals (Phase 6): replenishment cycles and
predicted next-disposal dates, one row per (resident, subject) where a
subject is either a waste category or a brand. Computed nightly by
services/profiling.py from the same gated detection history every Phase 6
feature reads through — see that module's docstring for the hard consent/
sensitivity/rejection gate every computation here must respect.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ConsumptionSignalSubjectType


class ConsumptionSignal(Base):
    __tablename__ = "consumption_signals"
    __table_args__ = (
        UniqueConstraint(
            "resident_id", "subject_type", "subject_value", name="uq_consumption_signal_subject"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject_type: Mapped[ConsumptionSignalSubjectType] = mapped_column(
        Enum(ConsumptionSignalSubjectType, name="consumption_signal_subject_type"), index=True
    )
    # A category name (Detection.category, today == bag_type) for CATEGORY
    # rows; a Brand.name for BRAND rows.
    subject_value: Mapped[str] = mapped_column(String(200))
    # The waste category this subject's disposals occur in — for CATEGORY
    # rows this is always equal to subject_value; for BRAND rows it's the
    # most common category among that brand's disposals for this resident.
    # Not in the phase's literal field list, but load-bearing: "brand A
    # stops and brand B starts in the SAME CATEGORY" (brand-switch
    # detection) can't be computed without knowing each brand signal's
    # category. See DECISIONS.md.
    category: Mapped[str] = mapped_column(String(50), index=True)

    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Every distinct date (ISO strings, ascending) this subject appeared in
    # a gated, trustworthy detection for this resident.
    disposal_dates: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    replenishment_cycle_days_mean: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    replenishment_cycle_days_stddev: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    last_disposal_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    predicted_next_disposal_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    # Null until observation_count >= 3 — see services/profiling.py's
    # MIN_OBSERVATIONS_FOR_CYCLE. 0.0-1.0: 1 - (stddev / mean), clamped —
    # a tight, regular cycle scores near 1; an erratic one scores near 0.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
