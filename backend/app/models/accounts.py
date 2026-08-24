"""People in the system.

`Resident` (table `users`) is a household whose waste we analyze — a data
subject, NOT a login account. `StaffAccount` is an authenticated platform user
(admin / station_operator / reviewer / analyst). Keeping them separate means PII
access control and auth concerns never mix. See DECISIONS.md #1.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encryption import EncryptedString, blind_index
from app.models.base import (
    Base,
    BuildingType,
    DietProfile,
    PreferredLanguage,
    PriceTier,
    ResidentStatus,
    StaffRole,
)


class Resident(Base):
    """A registered household resident. `name`, `phone`, and `address` are
    PII — reads are role-gated and logged to audit_log (see
    app.api.deps.require_pii_access), and the columns are encrypted at rest
    (Phase 7, closing DECISIONS.md #2).

    name/address use EncryptedString directly: nothing queries them by
    value, so transparent, non-deterministic encryption is all they need.
    phone is different — it has a uniqueness constraint and is looked up by
    exact match (GET /users/by-phone/{phone}), neither of which works
    against non-deterministic ciphertext. It's split into two columns:
    `_phone_encrypted` (the actual retrievable value, non-deterministic) and
    `phone_index` (a deterministic HMAC blind index — see
    app/core/encryption.py). The `phone` property below makes that split
    invisible to every existing caller: `resident.phone` still reads/writes
    a plain string exactly as before Phase 7, and keeps `phone_index` in
    sync automatically on write. The one thing that does NOT keep working
    unchanged is filtering by phone in a SQL query — `Resident.phone` is a
    plain Python property, not a column, so `.where(Resident.phone == x)`
    would silently do the wrong thing (compare a property object, not build
    SQL). Every such site must use `Resident.phone_index ==
    blind_index(x)` instead — see api/v1/residents.py.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(EncryptedString)
    _phone_encrypted: Mapped[str] = mapped_column("phone", EncryptedString)
    phone_index: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    address: Mapped[str] = mapped_column(EncryptedString)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def phone(self) -> str:
        return self._phone_encrypted

    @phone.setter
    def phone(self, value: str) -> None:
        self._phone_encrypted = value
        self.phone_index = blind_index(value)

    # --- Phase 1 domain extension: service operations + consent ---
    zone_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    building_type: Mapped[BuildingType | None] = mapped_column(
        Enum(BuildingType, name="building_type"), nullable=True
    )
    declared_household_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registered_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    qr_code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    preferred_language: Mapped[PreferredLanguage | None] = mapped_column(
        Enum(PreferredLanguage, name="preferred_language"), nullable=True
    )
    price_tier: Mapped[PriceTier | None] = mapped_column(
        Enum(PriceTier, name="price_tier"), nullable=True
    )
    diet_profile: Mapped[DietProfile | None] = mapped_column(
        Enum(DietProfile, name="diet_profile"), nullable=True
    )
    # Opt-in, never opt-out: consent_profiling starts False and only a
    # resident's explicit action should ever flip it True. See DECISIONS.md
    # for why this differs from consent_operational's default.
    consent_operational: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_profiling: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_captured_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[ResidentStatus] = mapped_column(
        Enum(ResidentStatus, name="resident_status"), default=ResidentStatus.ACTIVE
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


class ResidentBankDetail(Base):
    """A resident's bank account, for pickup-service payouts/refunds. 1—1
    with Resident, split into its own table rather than columns on Resident
    so it can be locked down independently of the rest of the PII surface.

    The raw account number is never stored in plaintext:
    `account_number_encrypted` holds ciphertext (encryption service lands
    with whichever phase adds a write path for this table — see
    DECISIONS.md); `account_last4` is the only human-readable fragment,
    kept specifically so staff can verify an account without decrypting.
    Unlike the rest of this project's PII (DECISIONS.md #2), this table's
    encryption is not deferred — see DECISIONS.md.

    Never appears in any Pydantic response schema.
    """

    __tablename__ = "resident_bank_details"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    account_holder_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_number_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
