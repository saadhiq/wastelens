"""Daily cap on vision API calls (CV_DAILY_CALL_CAP), tracked in Redis.

The counter is incremented BEFORE each model call; when the cap is exceeded
the analysis job fails the capture with a clear reason instead of silently
burning budget. Counters expire after two days.

daily_cost_usd() (Phase 2) is a separate, complementary signal: the call
counter above caps *volume* regardless of price; this sums actual
InferenceRun.cost_usd for real spend visibility. It doesn't gate anything
today — there's no configured dollar cap — it's just queryable.
"""

import datetime as dt
from decimal import Decimal
from typing import cast

import redis as redis_lib
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import InferenceRun


class CostCapExceeded(Exception):
    def __init__(self, cap: int) -> None:
        super().__init__(f"Daily CV call cap reached ({cap})")
        self.cap = cap


def register_cv_call() -> int:
    """Increment today's counter; raise CostCapExceeded when over the cap.
    Returns the call count so callers can log it."""
    settings = get_settings()
    key = f"cv_calls:{dt.datetime.now(dt.UTC):%Y%m%d}"
    r = redis_lib.Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    count = int(cast(int, r.incr(key)))
    if count == 1:
        r.expire(key, 60 * 60 * 48)
    if count > settings.cv_daily_call_cap:
        raise CostCapExceeded(settings.cv_daily_call_cap)
    return count


def daily_cost_usd(db: Session, day: dt.date | None = None) -> Decimal:
    """Sum InferenceRun.cost_usd for the given UTC day (default: today).
    cost_usd is currently None on every row (no per-token pricing table
    exists yet — see ProviderResponse), so this returns 0 until a provider
    starts populating it; the query is correct and ready for that."""
    day = day or dt.datetime.now(dt.UTC).date()
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    end = start + dt.timedelta(days=1)
    total = db.scalar(
        select(func.coalesce(func.sum(InferenceRun.cost_usd), 0)).where(
            InferenceRun.started_at >= start, InferenceRun.started_at < end
        )
    )
    # coalesce(...) guarantees a non-NULL row from the DB; the `or` is only
    # to satisfy mypy's view of Session.scalar()'s general Optional return.
    return Decimal(total or 0)
