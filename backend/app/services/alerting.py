"""Alerting (Phase 7): checks the failed-vision-run rate and daily spend
against configured thresholds, writing an Alert row for each breach.

No external notification channel exists anywhere in this project — no
Slack/email/PagerDuty integration was ever configured, and inventing one
that was never asked for isn't in scope. This is the honest, self-contained
implementation instead: a persisted, queryable alert surface (Alert model,
GET /analytics/alerts) plus a structured log line (log.warning, the same
observability pattern used throughout this codebase) for whatever log
aggregation the deployment already has. See DECISIONS.md.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models import Alert, AlertType, InferenceRun, InferenceRunStatus
from app.services.cost_guard import daily_cost_usd

log = get_logger(__name__)

# Don't write a second alert of the same type while an earlier one is still
# "fresh" — matches the hourly check-alerts schedule, so a persisting
# breach gets at most one new row per hour, not one per check.
_ALERT_DEDUPE_WINDOW = dt.timedelta(hours=1)


def _recently_alerted(db: Session, alert_type: AlertType) -> bool:
    since = dt.datetime.now(dt.UTC) - _ALERT_DEDUPE_WINDOW
    count = db.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.alert_type == alert_type, Alert.created_at >= since)
    )
    return bool(count)


def check_failed_run_rate(db: Session, *, window_hours: int = 1) -> Alert | None:
    """Failed InferenceRun share over the last window_hours. None (not
    zero) when there were no runs at all in the window — an idle system
    isn't a failing one."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=window_hours)
    total, failed = db.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(case((InferenceRun.status != InferenceRunStatus.SUCCESS, 1), else_=0)), 0
            ),
        ).where(InferenceRun.started_at >= since)
    ).one()

    if not total:
        return None

    rate = failed / total
    threshold = get_settings().alert_failed_run_rate_threshold
    if rate <= threshold or _recently_alerted(db, AlertType.FAILED_RUN_RATE):
        return None

    alert = Alert(
        alert_type=AlertType.FAILED_RUN_RATE,
        message=(
            f"Failed vision-run rate {rate:.0%} over the last {window_hours}h "
            f"({failed}/{total}) exceeds threshold {threshold:.0%}"
        ),
        metric_value=Decimal(str(round(rate, 4))),
        threshold=Decimal(str(threshold)),
    )
    db.add(alert)
    log.warning("alert_failed_run_rate", rate=rate, threshold=threshold, failed=failed, total=total)
    return alert


def check_daily_spend(db: Session) -> Alert | None:
    spend = daily_cost_usd(db)
    threshold = get_settings().alert_daily_spend_usd_threshold
    if spend <= threshold or _recently_alerted(db, AlertType.DAILY_SPEND):
        return None

    alert = Alert(
        alert_type=AlertType.DAILY_SPEND,
        message=f"Daily spend ${spend} exceeds threshold ${threshold}",
        metric_value=spend,
        threshold=Decimal(str(threshold)),
    )
    db.add(alert)
    log.warning("alert_daily_spend", spend=str(spend), threshold=str(threshold))
    return alert


def check_alerts(db: Session) -> list[Alert]:
    """Runs every configured check; returns whichever new alerts were
    written (commits them). Called hourly by the worker; see worker.py."""
    alerts = [a for a in (check_failed_run_rate(db), check_daily_spend(db)) if a is not None]
    db.commit()
    return alerts
