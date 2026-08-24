"""Schemas for admin-only operational endpoints (Phase 8)."""

from pydantic import BaseModel


class BackupResult(BaseModel):
    key: str
