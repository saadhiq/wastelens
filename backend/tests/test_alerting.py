"""Phase 7: alerting on failed-run rate and daily spend, with dedup so a
persisting breach doesn't spam a new alert every check."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import (
    Alert,
    AlertType,
    Bag,
    BagType,
    Capture,
    CollectionSession,
    InferenceRun,
    InferenceRunStatus,
    Resident,
)
from app.services.alerting import check_daily_spend, check_failed_run_rate
from tests.conftest import login, requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _capture(db) -> Capture:
    suffix = uuid.uuid4().hex[:10]
    resident = Resident(name="Alerting Test", phone=f"+9483{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"AL-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=BagType.organic,
        image_url=f"captures/{suffix}.jpg",
        station_id="st-al",
    )
    db.add(capture)
    db.flush()
    return capture


def _run(
    db,
    capture: Capture,
    *,
    status: InferenceRunStatus,
    model_name: str,
    attempt_no: int = 1,
    started_at=None,
    cost_usd=None,
):
    db.add(
        InferenceRun(
            capture_id=capture.id,
            attempt_no=attempt_no,
            provider_name="test",
            model_name=model_name,
            status=status,
            started_at=started_at or dt.datetime.now(dt.UTC),
            cost_usd=cost_usd,
        )
    )


class TestFailedRunRate:
    def test_no_alert_when_no_runs_in_window(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "alert_failed_run_rate_threshold", 0.1)
        # Use a model name unique to this test — the failed/total ratio is
        # computed globally, so a real breach elsewhere in the suite could
        # otherwise mask this specific "nothing happened" assertion. Since
        # there's genuinely nothing for this test's own window, the
        # function still operates on whatever's in the DB — this test only
        # asserts on the no-runs-at-all case being safe (no ZeroDivisionError).
        alert = check_failed_run_rate(db, window_hours=0)
        assert alert is None

    def test_alert_written_when_rate_exceeds_threshold(self, db, monkeypatch):
        # check_failed_run_rate is a genuinely global metric (no per-test
        # scoping) over a shared, non-rolled-back test DB — other tests in
        # the same session may have left real InferenceRun rows in the
        # last hour. Rather than assume a clean window, add a large
        # weight of FAILED runs: enough to dominate any plausible amount
        # of ambient successful activity from the rest of the suite, so
        # the combined rate provably clears the threshold either way.
        db.query(Alert).filter_by(alert_type=AlertType.FAILED_RUN_RATE).delete()
        db.commit()
        monkeypatch.setattr(get_settings(), "alert_failed_run_rate_threshold", 0.5)

        model = f"test-model-{uuid.uuid4().hex[:8]}"
        capture = _capture(db)
        for i in range(200):
            _run(
                db,
                capture,
                status=InferenceRunStatus.FAILED_PROVIDER_ERROR,
                model_name=model,
                attempt_no=i + 1,
            )
        db.commit()

        alert = check_failed_run_rate(db, window_hours=1)
        db.commit()
        assert alert is not None
        assert alert.alert_type == AlertType.FAILED_RUN_RATE

    def test_dedup_suppresses_a_second_alert_within_the_window(self, db, monkeypatch):
        db.query(Alert).filter_by(alert_type=AlertType.FAILED_RUN_RATE).delete()
        db.commit()
        monkeypatch.setattr(get_settings(), "alert_failed_run_rate_threshold", 0.5)

        model = f"test-model-{uuid.uuid4().hex[:8]}"
        capture = _capture(db)
        for i in range(200):
            _run(
                db,
                capture,
                status=InferenceRunStatus.FAILED_PROVIDER_ERROR,
                model_name=model,
                attempt_no=i + 1,
            )
        db.commit()

        first = check_failed_run_rate(db, window_hours=1)
        db.commit()
        assert first is not None

        second = check_failed_run_rate(db, window_hours=1)
        assert second is None


class TestDailySpend:
    def test_alert_written_when_spend_exceeds_threshold(self, db, monkeypatch):
        db.query(Alert).filter_by(alert_type=AlertType.DAILY_SPEND).delete()
        db.commit()
        monkeypatch.setattr(get_settings(), "alert_daily_spend_usd_threshold", 1.0)

        model = f"test-spend-{uuid.uuid4().hex[:8]}"
        capture = _capture(db)
        _run(
            db,
            capture,
            status=InferenceRunStatus.SUCCESS,
            model_name=model,
            cost_usd=Decimal("5.00"),
        )
        db.commit()

        alert = check_daily_spend(db)
        db.commit()
        assert alert is not None
        assert alert.alert_type == AlertType.DAILY_SPEND

    def test_no_alert_when_spend_within_threshold(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "alert_daily_spend_usd_threshold", 1_000_000.0)
        assert check_daily_spend(db) is None


class TestAlertsEndpoint:
    def test_requires_analyst_role(self, client):
        assert client.get("/api/v1/analytics/alerts").status_code == 401

    def test_returns_a_list(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        resp = client.get("/api/v1/analytics/alerts", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
