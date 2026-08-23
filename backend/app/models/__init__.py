"""All ORM models. Importing this package registers every table on Base.metadata,
which Alembic's env.py relies on for autogenerate support."""

from app.models.accounts import Resident, StaffAccount
from app.models.audit import AuditLog
from app.models.base import (
    AnalysisStatus,
    BagStatus,
    BagType,
    Base,
    ReviewStatus,
    StaffRole,
)
from app.models.catalog import Brand, VocabularyItem
from app.models.profiles import UserWasteProfile
from app.models.waste import Bag, Capture, CollectionSession, Detection

__all__ = [
    "AnalysisStatus",
    "AuditLog",
    "Bag",
    "BagStatus",
    "BagType",
    "Base",
    "Brand",
    "Capture",
    "CollectionSession",
    "Detection",
    "Resident",
    "ReviewStatus",
    "StaffAccount",
    "StaffRole",
    "UserWasteProfile",
    "VocabularyItem",
]
