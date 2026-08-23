"""Model-level tests for the Phase 1 domain model extension: new columns on
existing tables (defaults, constraints) and the new tables. No endpoints, no
UI, no pipeline — these test the ORM layer directly, same style as
test_pipeline.py's capture_fixture.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Bag,
    BagType,
    Bin,
    BinTransfer,
    BinType,
    BuildingType,
    CalendarDay,
    Capture,
    CollectionSession,
    Collector,
    Detection,
    DietProfile,
    InferenceRun,
    InferenceRunStatus,
    InspectionStation,
    ItemState,
    PickupChannel,
    PickupRequest,
    PickupStatus,
    PreferredLanguage,
    PriceTier,
    Resident,
    ResidentBankDetail,
    ResidentStatus,
    StaffAccount,
    StaffRole,
    UnmappedLabel,
    VocabularyItem,
)
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _resident(db, **overrides) -> Resident:
    suffix = _suffix()
    resident = Resident(
        name="Domain Ext Tester",
        phone=f"+9477{suffix[:7]}",
        address="1 Test Rd",
        **overrides,
    )
    db.add(resident)
    db.flush()
    return resident


# --- Resident: new columns + consent defaults ---


class TestResidentExtension:
    def test_new_columns_default_correctly(self, db):
        resident = _resident(db)
        db.commit()
        db.refresh(resident)

        # Opt-in, never opt-out (DECISIONS.md #12): profiling starts FALSE
        # even though operational consent starts TRUE.
        assert resident.consent_operational is True
        assert resident.consent_profiling is False
        assert resident.status == ResidentStatus.ACTIVE
        # Everything else added in Phase 1 is nullable and unset by default.
        assert resident.zone_code is None
        assert resident.building_type is None
        assert resident.qr_code is None
        assert resident.consent_captured_at is None

    def test_can_set_all_new_fields(self, db):
        resident = _resident(
            db,
            zone_code="Z-12",
            building_type=BuildingType.APARTMENT,
            declared_household_size=4,
            registered_date=dt.date(2026, 1, 1),
            qr_code=f"QR-{_suffix()}",
            preferred_language=PreferredLanguage.si,
            price_tier=PriceTier.PREMIUM,
            diet_profile=DietProfile.VEGETARIAN,
            consent_profiling=True,
            consent_captured_at=dt.datetime.now(dt.UTC),
        )
        db.commit()
        db.refresh(resident)
        assert resident.building_type == BuildingType.APARTMENT
        assert resident.diet_profile == DietProfile.VEGETARIAN
        assert resident.consent_profiling is True

    def test_qr_code_unique(self, db):
        code = f"QR-{_suffix()}"
        _resident(db, qr_code=code)
        db.commit()
        with pytest.raises(IntegrityError):
            _resident(db, qr_code=code)
            db.commit()
        db.rollback()


# --- ResidentBankDetail: 1-1, encrypted-shape column ---


class TestResidentBankDetail:
    def test_one_to_one_and_last4_readable_without_decrypting(self, db):
        resident = _resident(db)
        db.flush()
        bank = ResidentBankDetail(
            resident_id=resident.id,
            account_holder_name="Domain Ext Tester",
            bank_name="Test Bank",
            account_number_encrypted="ciphertext-not-a-real-account-number",
            account_last4="4242",
        )
        db.add(bank)
        db.commit()
        db.refresh(bank)

        assert bank.is_verified is False  # default
        assert bank.account_last4 == "4242"
        assert bank.account_number_encrypted != "4242"  # never the raw number

    def test_second_bank_detail_for_same_resident_rejected(self, db):
        resident = _resident(db)
        db.flush()
        db.add(ResidentBankDetail(resident_id=resident.id))
        db.commit()
        with pytest.raises(IntegrityError):
            db.add(ResidentBankDetail(resident_id=resident.id))
            db.commit()
        db.rollback()


# --- Bag: weight/condition + the net_weight_kg property ---


class TestBagExtension:
    def test_net_weight_kg_is_none_until_both_weights_set(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"TAG-{_suffix()}")
        db.add(bag)
        db.commit()
        assert bag.net_weight_kg is None

        bag.gross_weight_kg = Decimal("5.50")
        assert bag.net_weight_kg is None  # tare still unset

        bag.tare_weight_kg = Decimal("1.20")
        assert bag.net_weight_kg == Decimal("4.30")

    def test_net_weight_kg_is_not_a_column(self, db):
        # It must never show up as a mapped attribute a query could filter on.
        assert "net_weight_kg" not in Bag.__table__.columns


# --- CollectionSession, Capture, Detection extensions ---


class TestCaptureExtension:
    def test_duplicate_image_sha256_for_same_bag_rejected(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.paper, tag_id=f"TAG-{_suffix()}")
        session = CollectionSession(user_id=resident.id)
        db.add_all([bag, session])
        db.flush()

        sha = uuid.uuid4().hex
        db.add(
            Capture(
                session_id=session.id,
                bag_id=bag.id,
                bag_type=BagType.paper,
                image_url="captures/a.jpg",
                station_id="st-1",
                image_sha256=sha,
            )
        )
        db.commit()

        with pytest.raises(IntegrityError):
            db.add(
                Capture(
                    session_id=session.id,
                    bag_id=bag.id,
                    bag_type=BagType.paper,
                    image_url="captures/b.jpg",  # different URL, same hash+bag
                    station_id="st-1",
                    image_sha256=sha,
                )
            )
            db.commit()
        db.rollback()

    def test_same_image_sha256_allowed_for_a_different_bag(self, db):
        resident = _resident(db)
        db.flush()
        bag_a = Bag(user_id=resident.id, bag_type=BagType.paper, tag_id=f"TAG-{_suffix()}")
        bag_b = Bag(user_id=resident.id, bag_type=BagType.paper, tag_id=f"TAG-{_suffix()}")
        session = CollectionSession(user_id=resident.id)
        db.add_all([bag_a, bag_b, session])
        db.flush()

        sha = uuid.uuid4().hex
        db.add_all(
            [
                Capture(
                    session_id=session.id,
                    bag_id=bag_a.id,
                    bag_type=BagType.paper,
                    image_url="captures/a.jpg",
                    station_id="st-1",
                    image_sha256=sha,
                ),
                Capture(
                    session_id=session.id,
                    bag_id=bag_b.id,
                    bag_type=BagType.paper,
                    image_url="captures/b.jpg",
                    station_id="st-1",
                    image_sha256=sha,
                ),
            ]
        )
        db.commit()  # no error


class TestDetectionExtension:
    def test_brand_text_kept_independently_of_matched_brand_id(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.polythene, tag_id=f"TAG-{_suffix()}")
        session = CollectionSession(user_id=resident.id)
        db.add_all([bag, session])
        db.flush()
        capture = Capture(
            session_id=session.id,
            bag_id=bag.id,
            bag_type=BagType.polythene,
            image_url="captures/x.jpg",
            station_id="st-1",
        )
        db.add(capture)
        db.flush()

        detection = Detection(
            capture_id=capture.id,
            item_name="chips_packet",
            confidence=0.9,
            brand_text="Totally Unknown Snack Co",
            item_state=ItemState.FRESH_TRIM,
            is_contaminant=False,
            bbox_x=10,
            bbox_y=20,
            bbox_w=30,
            bbox_h=40,
        )
        db.add(detection)
        db.commit()
        db.refresh(detection)

        # brand_text preserved even though no Brand row matched it.
        assert detection.matched_brand_id is None
        assert detection.brand_text == "Totally Unknown Snack Co"
        assert detection.is_contaminant is False  # default
        assert detection.inference_run_id is None  # nullable for now, see DECISIONS.md #14


# --- VocabularyItem: category tree + sensitivity flags ---


class TestVocabularyItemExtension:
    def test_parent_child_and_sensitivity_defaults(self, db):
        suffix = _suffix()
        parent = VocabularyItem(
            bag_type=BagType.polythene, item_name=f"snacks_{suffix}", display_name="Snacks"
        )
        db.add(parent)
        db.flush()
        child = VocabularyItem(
            bag_type=BagType.polythene,
            item_name=f"chips_{suffix}",
            display_name="Chips",
            parent_id=parent.id,
        )
        db.add(child)
        db.commit()
        db.refresh(child)

        assert child.parent_id == parent.id
        assert child.is_contaminant_by_default is False
        assert child.is_sensitive is False

    def test_sensitive_item_flag(self, db):
        item = VocabularyItem(
            bag_type=BagType.general,
            item_name=f"medication_{_suffix()}",
            display_name="Medication",
            is_sensitive=True,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert item.is_sensitive is True


class TestUnmappedLabel:
    def test_unique_per_raw_label_and_bag_type(self, db):
        label = f"mystery_item_{_suffix()}"
        db.add(UnmappedLabel(raw_label=label, bag_type=BagType.organic))
        db.commit()
        with pytest.raises(IntegrityError):
            db.add(UnmappedLabel(raw_label=label, bag_type=BagType.organic))
            db.commit()
        db.rollback()

    def test_same_label_different_bag_type_allowed(self, db):
        label = f"mystery_item_{_suffix()}"
        db.add_all(
            [
                UnmappedLabel(raw_label=label, bag_type=BagType.organic),
                UnmappedLabel(raw_label=label, bag_type=BagType.general),
            ]
        )
        db.commit()  # no error

    def test_defaults(self, db):
        row = UnmappedLabel(raw_label=f"thing_{_suffix()}", bag_type=BagType.paper)
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.occurrence_count == 1
        assert row.resolved is False


# --- Operations: PickupRequest, Collector, InspectionStation, Bin, BinTransfer ---


class TestPickupRequest:
    def test_defaults_and_lifecycle_fields(self, db):
        resident = _resident(db)
        db.flush()
        request = PickupRequest(
            resident_id=resident.id,
            requested_for_date=dt.date.today(),
            channel=PickupChannel.WHATSAPP,
        )
        db.add(request)
        db.commit()
        db.refresh(request)

        assert request.status == PickupStatus.REQUESTED
        assert request.collection_session_id is None

    def test_can_link_to_fulfilling_session(self, db):
        resident = _resident(db)
        db.flush()
        session = CollectionSession(user_id=resident.id)
        db.add(session)
        db.flush()
        request = PickupRequest(
            resident_id=resident.id,
            requested_for_date=dt.date.today(),
            channel=PickupChannel.MOBILE_APP,
            status=PickupStatus.COMPLETED,
            collection_session_id=session.id,
        )
        db.add(request)
        db.commit()
        db.refresh(request)
        assert request.collection_session_id == session.id


class TestCollector:
    def test_one_to_one_with_staff_account(self, db):
        suffix = _suffix()
        staff = StaffAccount(
            email=f"collector-{suffix}@wastelens-test.io",
            full_name="Field Collector",
            hashed_password="not-a-real-hash",
            role=StaffRole.station_operator,
        )
        db.add(staff)
        db.flush()
        collector = Collector(
            staff_account_id=staff.id,
            employee_code=f"EMP-{suffix}",
            full_name="Field Collector",
        )
        db.add(collector)
        db.commit()
        db.refresh(collector)
        assert collector.is_active is True

    def test_second_collector_for_same_staff_account_rejected(self, db):
        suffix = _suffix()
        staff = StaffAccount(
            email=f"collector2-{suffix}@wastelens-test.io",
            full_name="Field Collector",
            hashed_password="not-a-real-hash",
            role=StaffRole.station_operator,
        )
        db.add(staff)
        db.flush()
        db.add(Collector(staff_account_id=staff.id, employee_code=f"A-{suffix}", full_name="A"))
        db.commit()
        with pytest.raises(IntegrityError):
            db.add(Collector(staff_account_id=staff.id, employee_code=f"B-{suffix}", full_name="B"))
            db.commit()
        db.rollback()


class TestInspectionStation:
    def test_create_and_unique_station_code(self, db):
        code = f"STN-{_suffix()}"
        db.add(InspectionStation(station_code=code, facility_name="Main Facility"))
        db.commit()
        with pytest.raises(IntegrityError):
            db.add(InspectionStation(station_code=code, facility_name="Main Facility"))
            db.commit()
        db.rollback()


class TestBinAndTransfer:
    def test_bin_transfer_records_the_handoff(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"TAG-{_suffix()}")
        bin_ = Bin(bin_code=f"BIN-{_suffix()}", bin_type=BinType.ORGANIC)
        db.add_all([bag, bin_])
        db.flush()

        transfer = BinTransfer(bag_id=bag.id, bin_id=bin_.id, weight_kg=Decimal("3.10"))
        db.add(transfer)
        db.commit()
        db.refresh(transfer)
        assert transfer.weight_kg == Decimal("3.10")

    def test_bin_with_transfer_history_cannot_be_deleted(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"TAG-{_suffix()}")
        bin_ = Bin(bin_code=f"BIN-{_suffix()}", bin_type=BinType.ORGANIC)
        db.add_all([bag, bin_])
        db.flush()
        db.add(BinTransfer(bag_id=bag.id, bin_id=bin_.id))
        db.commit()

        db.delete(bin_)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# --- InferenceRun: the per-attempt vision-model audit trail ---


class TestInferenceRun:
    def test_two_attempts_for_one_capture_both_preserved(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"TAG-{_suffix()}")
        session = CollectionSession(user_id=resident.id)
        db.add_all([bag, session])
        db.flush()
        capture = Capture(
            session_id=session.id,
            bag_id=bag.id,
            bag_type=BagType.organic,
            image_url="captures/x.jpg",
            station_id="st-1",
        )
        db.add(capture)
        db.flush()

        failed_attempt = InferenceRun(
            capture_id=capture.id,
            attempt_no=1,
            provider_name="nvidia",
            model_name="meta/llama-4-maverick-17b-128e-instruct",
            status=InferenceRunStatus.FAILED_INVALID_JSON,
            raw_text="not valid json",
            error_message="JSONDecodeError",
        )
        repair_attempt = InferenceRun(
            capture_id=capture.id,
            attempt_no=2,
            provider_name="nvidia",
            model_name="meta/llama-4-maverick-17b-128e-instruct",
            status=InferenceRunStatus.SUCCESS,
            overall_confidence=0.87,
            model_predicted_bag_type=BagType.organic,
        )
        db.add_all([failed_attempt, repair_attempt])
        db.commit()

        runs = (
            db.query(InferenceRun)
            .filter_by(capture_id=capture.id)
            .order_by(InferenceRun.attempt_no)
            .all()
        )
        assert len(runs) == 2
        assert runs[0].status == InferenceRunStatus.FAILED_INVALID_JSON
        assert runs[1].status == InferenceRunStatus.SUCCESS
        # The failed attempt's raw output is kept, not discarded.
        assert runs[0].raw_text == "not valid json"

    def test_duplicate_attempt_no_for_same_capture_rejected(self, db):
        resident = _resident(db)
        db.flush()
        bag = Bag(user_id=resident.id, bag_type=BagType.organic, tag_id=f"TAG-{_suffix()}")
        session = CollectionSession(user_id=resident.id)
        db.add_all([bag, session])
        db.flush()
        capture = Capture(
            session_id=session.id,
            bag_id=bag.id,
            bag_type=BagType.organic,
            image_url="captures/y.jpg",
            station_id="st-1",
        )
        db.add(capture)
        db.flush()

        db.add(
            InferenceRun(
                capture_id=capture.id,
                attempt_no=1,
                provider_name="nvidia",
                model_name="m",
                status=InferenceRunStatus.SUCCESS,
            )
        )
        db.commit()
        with pytest.raises(IntegrityError):
            db.add(
                InferenceRun(
                    capture_id=capture.id,
                    attempt_no=1,
                    provider_name="nvidia",
                    model_name="m",
                    status=InferenceRunStatus.TIMEOUT,
                )
            )
            db.commit()
        db.rollback()


class TestCalendarDay:
    def test_create_and_primary_key_is_the_date(self, db):
        # Use a far-future date so repeated test runs don't collide.
        day = dt.date(2099, 1, 1) + dt.timedelta(days=uuid.uuid4().int % 1000)
        row = CalendarDay(calendar_date=day, day_of_week=day.weekday(), is_weekend=False)
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.is_poya is False  # default
        assert row.is_public_holiday is False  # default
