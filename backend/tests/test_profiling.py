"""Phase 6 household consumption layer: the hard consent/sensitivity/
rejection gate, replenishment cycle computation, stale-signal cleanup,
packaged/fresh + spoiled-food ratios, brand loyalty, brand-switch
detection, and category churn (including travel-gap suppression)."""

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
    ConsumptionSignal,
    ConsumptionSignalSubjectType,
    Detection,
    ItemState,
    PickupChannel,
    PickupRequest,
    Resident,
    ReviewStatus,
    VocabularyItem,
)
from app.services.profiling import (
    MIN_OBSERVATIONS_FOR_CYCLE,
    compute_consumption_signals,
    detect_brand_switches,
    detect_churn_risk,
    get_consumption,
    get_predictions,
)
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _resident(db, *, consent: bool = True) -> Resident:
    suffix = uuid.uuid4().hex[:8]
    r = Resident(
        name="Consumption Test",
        phone=f"+9472{suffix[:7]}",
        address="x",
        consent_profiling=consent,
    )
    db.add(r)
    db.flush()
    return r


def _vocab(db, bag_type: BagType, item_name: str, *, is_sensitive: bool = False) -> None:
    existing = db.query(VocabularyItem).filter_by(bag_type=bag_type, item_name=item_name).first()
    if existing is None:
        db.add(
            VocabularyItem(
                bag_type=bag_type,
                item_name=item_name,
                display_name=item_name,
                is_sensitive=is_sensitive,
            )
        )
        db.flush()


def _detection(
    db,
    resident: Resident,
    *,
    bag_type: BagType,
    item_name: str,
    captured_at: dt.datetime,
    confidence: float = 0.9,
    review_status: ReviewStatus = ReviewStatus.unreviewed,
    matched_brand_id: uuid.UUID | None = None,
    item_state: ItemState | None = None,
) -> Detection:
    suffix = uuid.uuid4().hex[:10]
    bag = Bag(user_id=resident.id, bag_type=bag_type, tag_id=f"CT-{suffix}")
    session = CollectionSession(user_id=resident.id)
    db.add_all([bag, session])
    db.flush()
    capture = Capture(
        session_id=session.id,
        bag_id=bag.id,
        bag_type=bag_type,
        image_url=f"captures/{suffix}.jpg",
        station_id="st-ct",
        captured_at=captured_at,
    )
    db.add(capture)
    db.flush()
    detection = Detection(
        capture_id=capture.id,
        item_name=item_name,
        category=bag_type.value,
        confidence=confidence,
        review_status=review_status,
        matched_brand_id=matched_brand_id,
        item_state=item_state,
    )
    db.add(detection)
    db.commit()
    return detection


def _days_ago(n: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=n)


class TestHardGate:
    def test_excludes_residents_without_consent(self, db):
        _vocab(db, BagType.organic, "banana_peel")
        consenting = _resident(db, consent=True)
        non_consenting = _resident(db, consent=False)
        for r in (consenting, non_consenting):
            for i in range(3):
                _detection(
                    db,
                    r,
                    bag_type=BagType.organic,
                    item_name="banana_peel",
                    captured_at=_days_ago(21 - i * 7),
                )

        compute_consumption_signals(db)

        assert db.query(ConsumptionSignal).filter_by(resident_id=consenting.id).count() > 0
        assert db.query(ConsumptionSignal).filter_by(resident_id=non_consenting.id).count() == 0

    def test_excludes_sensitive_vocabulary_items(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "sensitive_thing", is_sensitive=True)
        _vocab(db, BagType.organic, "banana_peel", is_sensitive=False)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="sensitive_thing",
                captured_at=_days_ago(21 - i * 7),
            )
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=_days_ago(21 - i * 7),
            )

        compute_consumption_signals(db)

        signal = (
            db.query(ConsumptionSignal)
            .filter_by(
                resident_id=resident.id,
                subject_type=ConsumptionSignalSubjectType.CATEGORY,
                subject_value=BagType.organic.value,
            )
            .one()
        )
        # Only the non-sensitive item's 3 dates count — the sensitive
        # item's 3 disposals (same dates) must not inflate this.
        assert signal.observation_count == 3

    def test_excludes_rejected_detections(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=_days_ago(21 - i * 7),
                review_status=ReviewStatus.rejected,
            )

        compute_consumption_signals(db)

        assert db.query(ConsumptionSignal).filter_by(resident_id=resident.id).count() == 0


class TestCycleComputation:
    def test_below_minimum_observations_yields_no_prediction(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        assert MIN_OBSERVATIONS_FOR_CYCLE == 3
        for i in range(2):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=_days_ago(14 - i * 7),
            )

        compute_consumption_signals(db)

        signal = (
            db.query(ConsumptionSignal)
            .filter_by(
                resident_id=resident.id,
                subject_type=ConsumptionSignalSubjectType.CATEGORY,
            )
            .one()
        )
        assert signal.observation_count == 2
        assert signal.replenishment_cycle_days_mean is None
        assert signal.confidence is None
        assert signal.predicted_next_disposal_date is None

    def test_regular_cycle_yields_high_confidence_prediction(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        # Exactly 7 days apart, three times -> mean=7, stddev=0, confidence=1.
        base = _days_ago(14)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=base + dt.timedelta(days=7 * i),
            )

        compute_consumption_signals(db)

        signal = (
            db.query(ConsumptionSignal)
            .filter_by(
                resident_id=resident.id,
                subject_type=ConsumptionSignalSubjectType.CATEGORY,
            )
            .one()
        )
        assert signal.observation_count == 3
        assert float(signal.replenishment_cycle_days_mean) == 7.0
        assert float(signal.replenishment_cycle_days_stddev) == 0.0
        assert float(signal.confidence) == 1.0
        expected_next = (base + dt.timedelta(days=14)).date() + dt.timedelta(days=7)
        assert signal.predicted_next_disposal_date == expected_next


class TestStaleSignalCleanup:
    def test_revoking_consent_removes_existing_signals(self, db):
        resident = _resident(db, consent=True)
        _vocab(db, BagType.organic, "banana_peel")
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=_days_ago(21 - i * 7),
            )
        compute_consumption_signals(db)
        assert db.query(ConsumptionSignal).filter_by(resident_id=resident.id).count() > 0

        resident.consent_profiling = False
        db.commit()
        compute_consumption_signals(db)

        assert db.query(ConsumptionSignal).filter_by(resident_id=resident.id).count() == 0

    def test_subject_falling_out_of_gate_is_removed(self, db):
        resident = _resident(db)
        _vocab(db, BagType.paper, "newspaper")
        detections = [
            _detection(
                db,
                resident,
                bag_type=BagType.paper,
                item_name="newspaper",
                captured_at=_days_ago(21 - i * 7),
            )
            for i in range(3)
        ]
        compute_consumption_signals(db)
        assert (
            db.query(ConsumptionSignal)
            .filter_by(resident_id=resident.id, subject_value=BagType.paper.value)
            .count()
            == 1
        )

        for d in detections:
            live = db.get(Detection, d.id)
            live.review_status = ReviewStatus.rejected
        db.commit()
        compute_consumption_signals(db)

        assert (
            db.query(ConsumptionSignal)
            .filter_by(resident_id=resident.id, subject_value=BagType.paper.value)
            .count()
            == 0
        )


class TestPackagedVsFreshAndSpoiled:
    def test_ratio_and_spoiled_share(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        _vocab(db, BagType.polythene, "chips_packet")

        _detection(
            db,
            resident,
            bag_type=BagType.organic,
            item_name="banana_peel",
            captured_at=_days_ago(1),
            item_state=ItemState.FRESH_TRIM,
        )
        _detection(
            db,
            resident,
            bag_type=BagType.organic,
            item_name="banana_peel",
            captured_at=_days_ago(2),
            item_state=ItemState.SPOILED,
        )
        _detection(
            db,
            resident,
            bag_type=BagType.organic,
            item_name="banana_peel",
            captured_at=_days_ago(3),
            item_state=None,
        )
        _detection(
            db,
            resident,
            bag_type=BagType.polythene,
            item_name="chips_packet",
            captured_at=_days_ago(1),
        )

        compute_consumption_signals(db)
        result = get_consumption(db, resident.id)
        assert result is not None
        # 3 organic (fresh) + 1 polythene (packaged) -> packaged share = 1/4
        assert result["packaged_vs_fresh_ratio"] == pytest.approx(0.25)
        # Of the 2 organic detections with a known state, 1 is spoiled.
        assert result["spoiled_food_share"] == pytest.approx(0.5)

    def test_none_when_no_data(self, db):
        resident = _resident(db)
        result = get_consumption(db, resident.id)
        assert result is not None
        assert result["packaged_vs_fresh_ratio"] is None
        assert result["spoiled_food_share"] is None


class TestBrandLoyalty:
    def test_herfindahl_reflects_concentration(self, db):
        resident = _resident(db)
        _vocab(db, BagType.polythene, "chips_packet")
        brand_a = Brand(name=f"LoyalBrand-{uuid.uuid4().hex[:6]}")
        brand_b = Brand(name=f"RivalBrand-{uuid.uuid4().hex[:6]}")
        db.add_all([brand_a, brand_b])
        db.flush()

        # Brand A: 4 disposals, Brand B: 1 -> shares 0.8/0.2 -> HHI = 0.68.
        for i in range(4):
            _detection(
                db,
                resident,
                bag_type=BagType.polythene,
                item_name="chips_packet",
                captured_at=_days_ago(20 - i * 5),
                matched_brand_id=brand_a.id,
            )
        _detection(
            db,
            resident,
            bag_type=BagType.polythene,
            item_name="chips_packet",
            captured_at=_days_ago(1),
            matched_brand_id=brand_b.id,
        )

        compute_consumption_signals(db)
        result = get_consumption(db, resident.id)
        assert result is not None
        loyalty = result["brand_loyalty"][BagType.polythene.value]
        assert loyalty["herfindahl_index"] == pytest.approx(0.68, abs=0.01)


class TestPredictions:
    def test_due_items_returned_ordered(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        base = _days_ago(21)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=base + dt.timedelta(days=7 * i),
            )
        compute_consumption_signals(db)

        # Predicted next = last (14 days ago) + 7 = 7 days ago -> already due.
        due = get_predictions(db, resident.id)
        assert len(due) == 1
        assert due[0].subject_value == BagType.organic.value

    def test_empty_for_non_consenting_resident(self, db):
        resident = _resident(db, consent=False)
        assert get_predictions(db, resident.id) == []

    def test_empty_for_unknown_resident(self, db):
        assert get_predictions(db, uuid.uuid4()) == []


class TestBrandSwitches:
    def test_detects_stopped_and_started_brand_in_same_category(self, db):
        resident = _resident(db)
        _vocab(db, BagType.polythene, "chips_packet")
        brand_a = Brand(name=f"OldBrand-{uuid.uuid4().hex[:6]}")
        brand_b = Brand(name=f"NewBrand-{uuid.uuid4().hex[:6]}")
        db.add_all([brand_a, brand_b])
        db.flush()

        # Brand A: regular (3+ obs), last seen 40 days ago (before a 30-day window).
        base = _days_ago(60)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.polythene,
                item_name="chips_packet",
                captured_at=base + dt.timedelta(days=10 * i),
                matched_brand_id=brand_a.id,
            )
        # Brand B: first appearance 10 days ago, inside the 30-day window.
        _detection(
            db,
            resident,
            bag_type=BagType.polythene,
            item_name="chips_packet",
            captured_at=_days_ago(10),
            matched_brand_id=brand_b.id,
        )

        compute_consumption_signals(db)
        events = detect_brand_switches(db, days=30)
        match = [
            e for e in events if e["resident_id"] == resident.id and e["brand_from"] == brand_a.name
        ]
        assert len(match) == 1
        assert match[0]["brand_to"] == brand_b.name
        assert match[0]["category"] == BagType.polythene.value

    def test_no_switch_when_only_one_brand_ever_seen(self, db):
        resident = _resident(db)
        _vocab(db, BagType.polythene, "chips_packet")
        brand = Brand(name=f"OnlyBrand-{uuid.uuid4().hex[:6]}")
        db.add(brand)
        db.flush()
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.polythene,
                item_name="chips_packet",
                captured_at=_days_ago(20 - i * 5),
                matched_brand_id=brand.id,
            )
        compute_consumption_signals(db)
        events = detect_brand_switches(db, days=30)
        assert not any(e["resident_id"] == resident.id for e in events)


class TestChurnRisk:
    def test_flags_churn_when_pickups_occurred(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        # Regular weekly cycle (mean=7) ending 30 days ago -> 30 > 2.5*7=17.5.
        base = _days_ago(44)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=base + dt.timedelta(days=7 * i),
            )
        compute_consumption_signals(db)
        signal = (
            db.query(ConsumptionSignal)
            .filter_by(
                resident_id=resident.id,
                subject_type=ConsumptionSignalSubjectType.CATEGORY,
            )
            .one()
        )

        # A pickup request after the last disposal proves the household was
        # reachable — this is real churn, not a travel gap.
        db.add(
            PickupRequest(
                resident_id=resident.id,
                requested_for_date=(signal.last_disposal_date + dt.timedelta(days=5)),
                channel=PickupChannel.PHONE,
            )
        )
        db.commit()

        results = detect_churn_risk(db)
        assert any(r["resident_id"] == resident.id for r in results)

    def test_suppresses_churn_when_no_pickups_at_all(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        base = _days_ago(44)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=base + dt.timedelta(days=7 * i),
            )
        compute_consumption_signals(db)

        # No PickupRequest at all for this resident -> travel gap, suppressed.
        results = detect_churn_risk(db)
        assert not any(r["resident_id"] == resident.id for r in results)

    def test_not_flagged_within_cycle_tolerance(self, db):
        resident = _resident(db)
        _vocab(db, BagType.organic, "banana_peel")
        # Last disposal only 10 days ago against a 7-day cycle: 10 <= 2.5*7.
        base = _days_ago(24)
        for i in range(3):
            _detection(
                db,
                resident,
                bag_type=BagType.organic,
                item_name="banana_peel",
                captured_at=base + dt.timedelta(days=7 * i),
            )
        compute_consumption_signals(db)
        results = detect_churn_risk(db)
        assert not any(r["resident_id"] == resident.id for r in results)
