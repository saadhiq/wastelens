"""Operational alerts (Phase 7): failed-vision-run rate and daily spend
breaches. No external notification channel exists anywhere in this
project (no Slack/email/PagerDuty integration was ever configured) — this
table is the alert surface itself: written by services/alerting.py,
read via GET /analytics/alerts. See DECISIONS.md.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DateTime, Enum, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AlertType, Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_type: Mapped[AlertType] = mapped_column(Enum(AlertType, name="alert_type"), index=True)
    message: Mapped[str] = mapped_column(Text)
    metric_value: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    threshold: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
