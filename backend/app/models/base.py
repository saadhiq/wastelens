"""Declarative base and shared enums for the WasteLens domain model.

Enums are defined once here so SQLAlchemy models, Pydantic schemas, and the
Alembic migration all reference the same values.
"""

import enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class BagType(enum.StrEnum):
    organic = "organic"
    polythene = "polythene"
    paper = "paper"
    general = "general"


class BagStatus(enum.StrEnum):
    registered = "registered"
    collected = "collected"
    processed = "processed"


class AnalysisStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class ReviewStatus(enum.StrEnum):
    unreviewed = "unreviewed"
    confirmed = "confirmed"
    corrected = "corrected"
    rejected = "rejected"


class StaffRole(enum.StrEnum):
    admin = "admin"
    station_operator = "station_operator"
    reviewer = "reviewer"
    analyst = "analyst"
    # Phase 4: field staff who run a collection route. Added here (not a new
    # enum) so it gets the same login/JWT/RBAC machinery as every other role
    # for free — a Collector row (operations.py) is the 1-1 profile that
    # holds route-specific fields this enum member alone doesn't carry.
    collector = "collector"


# --- Phase 1 domain model extension ---
# Values below are UPPERCASE by design, unlike the enums above: they're
# copied verbatim from the facility's own operations spec (zones, pickup
# channels, bin types, ...) rather than chosen here, so they're kept exactly
# as given instead of normalized to this file's original lowercase
# convention — that keeps them traceable back to that source of truth.
# preferred_language is the one exception: ISO-639-1-style language codes
# are conventionally lowercase, so it stays lowercase like the enums above.


class BuildingType(enum.StrEnum):
    HOUSE = "HOUSE"
    ANNEX = "ANNEX"
    APARTMENT = "APARTMENT"


class PriceTier(enum.StrEnum):
    ECONOMY = "ECONOMY"
    MID = "MID"
    PREMIUM = "PREMIUM"


class DietProfile(enum.StrEnum):
    MIXED = "MIXED"
    VEGETARIAN = "VEGETARIAN"
    SEAFOOD = "SEAFOOD"
    MEAT = "MEAT"


class ResidentStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


class PreferredLanguage(enum.StrEnum):
    si = "si"
    ta = "ta"
    en = "en"


class BagCondition(enum.StrEnum):
    GOOD = "GOOD"
    WET = "WET"
    TORN = "TORN"
    OVERFILLED = "OVERFILLED"


class LightingCondition(enum.StrEnum):
    OVERHEAD_LED = "OVERHEAD_LED"
    MIXED_DAYLIGHT = "MIXED_DAYLIGHT"


class ItemState(enum.StrEnum):
    FRESH_TRIM = "FRESH_TRIM"
    RIPE = "RIPE"
    OVERRIPE = "OVERRIPE"
    SPOILED = "SPOILED"
    MOULDY = "MOULDY"


class PickupChannel(enum.StrEnum):
    MOBILE_APP = "MOBILE_APP"
    WHATSAPP = "WHATSAPP"
    IVR = "IVR"
    PHONE = "PHONE"


class PickupStatus(enum.StrEnum):
    REQUESTED = "REQUESTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    MISSED = "MISSED"


class InferenceRunStatus(enum.StrEnum):
    SUCCESS = "SUCCESS"
    FAILED_INVALID_JSON = "FAILED_INVALID_JSON"
    FAILED_PROVIDER_ERROR = "FAILED_PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"


class BinType(enum.StrEnum):
    """Deliberately a separate enum from BagType, not a reuse of it — a
    physical downstream Bin's type is a different concept from a household
    bag's type, even though today's values happen to line up 1:1."""

    ORGANIC = "ORGANIC"
    PAPER = "PAPER"
    POLYTHENE = "POLYTHENE"
    GENERAL = "GENERAL"


class ReviewVerdict(enum.StrEnum):
    """A HumanReview's decision (Phase 3). Deliberately a separate enum from
    ReviewStatus above, not a reuse of it, despite the overlapping words:
    ReviewStatus is Detection's current, mutable state (and includes
    `unreviewed`, which no HumanReview row ever represents — a HumanReview
    only exists once a decision has been made); ReviewVerdict is the
    immutable record of what a specific reviewer decided at a specific time.
    UPPERCASE like Phase 1's other new-in-this-project enums, following that
    same precedent (see base.py's Phase 1 section)."""

    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class UnmappedLabelKind(enum.StrEnum):
    """What kind of unmatched label an UnmappedLabel row represents (Phase
    5) — the same table now serves two different queues that must never be
    confused: an ITEM row is a candidate VocabularyItem (the original,
    Phase-1 use); a BRAND row is packaging brand_text that didn't
    fuzzy-match any Brand (new in Phase 5, from paper/polythene
    extraction). Promoting a BRAND row into VocabularyItem would be wrong,
    so the discriminator is load-bearing, not informational — see
    api/v1/vocabulary.py's promote_unmapped_label guard."""

    ITEM = "ITEM"
    BRAND = "BRAND"


class ConsumptionSignalSubjectType(enum.StrEnum):
    """What a ConsumptionSignal row tracks (Phase 6): a CATEGORY row's
    subject_value is a waste category (Detection.category — today literally
    the bag_type string); a BRAND row's subject_value is a Brand.name. Both
    kinds share identical replenishment-cycle fields — a category and a
    brand are both just "a recurring thing a resident disposes of", tracked
    the same way, at two different levels of specificity."""

    CATEGORY = "CATEGORY"
    BRAND = "BRAND"


class AlertType(enum.StrEnum):
    """What an Alert row (Phase 7) is flagging — see services/alerting.py."""

    FAILED_RUN_RATE = "FAILED_RUN_RATE"
    DAILY_SPEND = "DAILY_SPEND"
