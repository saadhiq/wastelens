"""Fixed-window rate limiting (Phase 7), Redis-backed — same broker
already used for Celery and the daily-call cost cap (cost_guard.py),
following that module's exact pattern (INCR + EXPIRE on a bucketed key).

Applied to capture uploads: bounds how fast one station account can push
work into the CV pipeline, protecting the daily-call cap and downstream
spend from a runaway or misbehaving station script.
"""

import time
from typing import cast

import redis as redis_lib

from app.config import get_settings


class RateLimitExceeded(Exception):
    def __init__(self, limit: int, window_seconds: int) -> None:
        super().__init__(f"Rate limit exceeded: {limit} per {window_seconds}s")
        self.limit = limit
        self.window_seconds = window_seconds


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> int:
    """Increment key's fixed-window counter; raise RateLimitExceeded when
    over the limit. Returns the current count in this window."""
    settings = get_settings()
    window_id = int(time.time() // window_seconds)
    redis_key = f"rate_limit:{key}:{window_id}"

    r = redis_lib.Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    count = int(cast(int, r.incr(redis_key)))
    if count == 1:
        r.expire(redis_key, window_seconds * 2)
    if count > limit:
        raise RateLimitExceeded(limit, window_seconds)
    return count
