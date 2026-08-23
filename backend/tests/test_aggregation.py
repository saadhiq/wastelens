"""Aggregation-job tests: the quality rule, corrected-label precedence, and
the profile numbers. Requires the test database."""

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    Bag,
    BagType,
    Brand,
    Capture,
    CollectionSession,
    Detection,
    Resident,
    ReviewStatus,
    UserWasteProfile,
)
from app.services.aggregation import rebuild_week, week_start_of
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _detection(capture_id, item, conf, category="organic", **kw):
    return Detection(
        capture_id=capture_id, item_name=item, confidence=conf, category=category, **kw
    )


@pytest.fixture()
def week_data(db):
    """One resident, one organic + one polythene capture this week, with a mix
    of trustworthy / untrustworthy / corrected / rejected detections."""
    suffix = uuid.uuid4().hex[:8]
    resident = Resident(name="Agg", phone=f"+9478{suffix[:7]}", address="x")
    db.add(resident)
    db.flush()
    session = CollectionSession(user_id=resident.id)
    bag_o = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"AGG-O-{suffix}")
    bag_p = Bag(user_id=resident.id, bag_type=BagType.polythene, tag_id=f"AGG-P-{suffix}")
    brand = db.query(Brand).filter_by(name="Munchee").first()
    if brand is None:
        brand = Brand(name="Munchee", aliases=[], category="biscuits")
        db.add(brand)
    db.add_all([session, bag_o, bag_p])
    db.flush()

    cap_o = Capture(
        session_id=session.id,
        bag_id=bag_o.id,
        bag_type=BagType.organic,
        image_url="x",
        station_id="s",
    )
    cap_p = Capture(
        session_id=session.id,
        bag_id=bag_p.id,
        bag_type=BagType.polythene,
        image_url="x",
        station_id="s",
    )
    db.add_all([cap_o, cap_p])
    db.flush()

    db.add_all(
        [
            # counts: high confidence organic
            _detection(cap_o.id, "banana_peel", 0.9),
            _detection(cap_o.id, "banana_peel", 0.85),
            # counts: low confidence BUT human-corrected to onion_peel
            _detection(
                cap_o.id,
                "carrot_top",
                0.4,
                review_status=ReviewStatus.corrected,
                corrected_item_name="onion_peel",
            ),
            # does NOT count: low confidence, unreviewed
            _detection(cap_o.id, "tomato", 0.5),
            # does NOT count: rejected by reviewer despite high confidence
            _detection(cap_o.id, "egg_shell", 0.95, review_status=ReviewStatus.rejected),
            # counts toward categories only (unidentified is excluded from vegs)
            _detection(cap_o.id, "unidentified_item", 0.9),
            # counts: packaged food with a brand
            _detection(
                cap_p.id,
                "chips_packet",
                0.88,
                category="polythene",
                matched_brand_id=brand.id,
            ),
        ]
    )
    db.commit()
    return resident.id


def test_rebuild_week_applies_quality_rule(db, week_data):
    week = week_start_of(dt.datetime.now(dt.UTC))
    written = rebuild_week(db, week)
    assert written >= 1

    profile = db.query(UserWasteProfile).filter_by(user_id=week_data, week_start=week).one()

    # banana_peel x2 + corrected onion_peel = 3 veg detections.
    # tomato (low conf) and egg_shell (rejected) must NOT count.
    assert profile.veg_frequency == 3
    top = {v["item"]: v["count"] for v in profile.top_vegetables}
    assert top == {"banana_peel": 2, "onion_peel": 1}

    assert profile.packaged_food_frequency == 1
    assert profile.top_brands[0]["brand"] == "Munchee"

    # Category breakdown counts trustworthy detections incl. unidentified.
    assert profile.category_breakdown["organic"] == 4  # 2 banana + corrected + unidentified
    assert profile.category_breakdown["polythene"] == 1


def test_rebuild_is_idempotent(db, week_data):
    week = week_start_of(dt.datetime.now(dt.UTC))
    rebuild_week(db, week)
    rebuild_week(db, week)  # run twice — must update, not duplicate
    rows = db.query(UserWasteProfile).filter_by(user_id=week_data, week_start=week).all()
    assert len(rows) == 1


def test_week_start_is_monday():
    assert week_start_of(dt.datetime(2026, 7, 12, 10, 0)).weekday() == 0  # a Sunday -> Monday
    assert week_start_of(dt.datetime(2026, 7, 6, 0, 0)) == dt.date(2026, 7, 6)  # Monday stays
