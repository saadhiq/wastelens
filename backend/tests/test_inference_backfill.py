"""Tests for the Phase 2 backfill: every pre-existing Capture-with-Detections
gets a synthetic InferenceRun, and no Detection is left orphaned. Same
db/db_engine fixture style as test_pipeline.py.

All assertions here are scoped to the captures each test creates itself,
never to a whole-table count — the `db` fixture doesn't roll back between
tests (same as test_pipeline.py), and other test modules in the same run
(e.g. test_domain_extension.py) create their own Detection rows without
ever calling the backfill, so a global "0 orphaned" or "N created" count
would be order-dependent and flaky.
"""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    AnalysisStatus,
    Bag,
    BagType,
    Capture,
    CollectionSession,
    Detection,
    InferenceRun,
    InferenceRunStatus,
    Resident,
)
from app.services.inference_backfill import (
    BACKFILL_NOTE,
    backfill_missing_inference_runs,
    undo_backfill_missing_inference_runs,
)
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _capture_with_detections(db, n_detections: int = 2) -> Capture:
    suffix = uuid.uuid4().hex[:8]
    resident = Resident(name="P", phone=f"+9477{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"TAG-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=BagType.organic,
        image_url="captures/legacy.jpg",
        station_id="st-1",
        analysis_status=AnalysisStatus.done,
    )
    db.add(capture)
    db.flush()
    for _ in range(n_detections):
        db.add(
            Detection(capture_id=capture.id, item_name="carrot", confidence=0.9, category="organic")
        )
    db.commit()
    return capture


def test_backfill_leaves_zero_orphaned_detections(db):
    capture_a = _capture_with_detections(db, n_detections=2)
    capture_b = _capture_with_detections(db, n_detections=3)

    backfill_missing_inference_runs(db)

    for capture in (capture_a, capture_b):
        run = db.query(InferenceRun).filter_by(capture_id=capture.id).one()
        assert run.attempt_no == 1
        assert run.error_message == BACKFILL_NOTE
        assert run.raw_response is None
        detections = db.query(Detection).filter_by(capture_id=capture.id).all()
        assert detections  # sanity: the fixture actually created some
        assert all(d.inference_run_id == run.id for d in detections)


def test_backfill_skips_captures_that_already_have_a_run(db):
    capture = _capture_with_detections(db, n_detections=1)
    existing_run = InferenceRun(
        capture_id=capture.id,
        attempt_no=1,
        provider_name="nvidia",
        model_name="real-model",
        status=InferenceRunStatus.SUCCESS,
    )
    db.add(existing_run)
    db.commit()

    backfill_missing_inference_runs(db)

    # Exactly one run for this capture, and it's the real one — no
    # synthetic backfill row was added alongside it.
    runs = db.query(InferenceRun).filter_by(capture_id=capture.id).all()
    assert len(runs) == 1
    assert runs[0].id == existing_run.id
    assert runs[0].error_message != BACKFILL_NOTE


def test_backfill_is_idempotent(db):
    capture = _capture_with_detections(db)
    backfill_missing_inference_runs(db)
    run_after_first_call = db.query(InferenceRun).filter_by(capture_id=capture.id).one()

    backfill_missing_inference_runs(db)  # second call: must not add another row

    runs = db.query(InferenceRun).filter_by(capture_id=capture.id).all()
    assert len(runs) == 1
    assert runs[0].id == run_after_first_call.id


def test_undo_backfill_removes_only_backfilled_runs(db):
    capture_a = _capture_with_detections(db)
    capture_b = _capture_with_detections(db)
    backfill_missing_inference_runs(db)

    # A real run added after the backfill must survive the undo.
    real_run = InferenceRun(
        capture_id=capture_b.id,
        attempt_no=2,
        provider_name="nvidia",
        model_name="real-model",
        status=InferenceRunStatus.SUCCESS,
    )
    db.add(real_run)
    db.commit()

    undo_backfill_missing_inference_runs(db)

    assert db.query(InferenceRun).filter_by(capture_id=capture_a.id).count() == 0
    detections_a = db.query(Detection).filter_by(capture_id=capture_a.id).all()
    assert detections_a
    assert all(d.inference_run_id is None for d in detections_a)

    remaining_b = db.query(InferenceRun).filter_by(capture_id=capture_b.id).all()
    assert len(remaining_b) == 1
    assert remaining_b[0].id == real_run.id
