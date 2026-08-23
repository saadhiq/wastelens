"""People in the system.

`Resident` (table `users`) is a household whose waste we analyze — a data
subject, NOT a login account. `StaffAccount` is an authenticated platform user
(admin / station_operator / reviewer / analyst). Keeping them separate means PII
access control and auth concerns never mix. See DECISIONS.md #1.
"""

import datetime as dt
import uuid

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, StaffRole


class Resident(Base):
    """A registered household resident. `name` and `phone` are PII — reads are
    role-gated and logged to audit_log (see app.api.deps.require_pii_access)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    address: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StaffAccount(Base):
    """A platform login account with a role. Not a resident."""

    __tablename__ = "staff_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    hashed_password: Mapped[str] = mapped_column(String(200))
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole, name="staff_role"))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
