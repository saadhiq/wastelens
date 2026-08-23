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
