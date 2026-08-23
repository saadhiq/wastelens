"""WasteLens API entrypoint.

Wires up routing, CORS, structured logging with request IDs, and the consistent
error envelope ({"error": {"code", "message"}, "request_id"}) for all failures.
"""

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.config import get_settings
from app.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Best-effort startup: make sure the captures bucket exists. Failure is
    logged, not fatal — readiness probes surface storage problems."""
    try:
        from app.services import storage

        storage.ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        log.warning("bucket_ensure_failed", error=type(exc).__name__)
    yield


app = FastAPI(
    title="WasteLens API",
    version="0.1.0",
    description="Waste-intelligence platform: capture, detect, review, aggregate.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Bind a request_id to every log line and echo it in the response header."""
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


def _error_body(request: Request, code: str, message: str) -> dict:
    return {
        "error": {"code": code, "message": message},
        "request_id": getattr(request.state, "request_id", None),
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(request, code=f"http_{exc.status_code}", message=str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(request, code="validation_error", message=str(exc.errors())),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(request, code="internal_error", message="Internal server error"),
    )


app.include_router(api_router)
