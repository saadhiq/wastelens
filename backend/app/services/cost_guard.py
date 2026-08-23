"""Daily cap on vision API calls (CV_DAILY_CALL_CAP), tracked in Redis.

The counter is incremented BEFORE each model call; when the cap is exceeded
the analysis job fails the capture with a clear reason instead of silently
burning budget. Counters expire after two days.
"""

import datetime as dt
from typing import cast

import redis as redis_lib

from app.config import get_settings


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
