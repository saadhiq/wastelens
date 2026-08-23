"""Shared API envelope schemas: consistent error shape and pagination wrapper
used by every list endpoint."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """Every non-2xx response body has this shape."""

    error: ErrorDetail
    request_id: str | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
