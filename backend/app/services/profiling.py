"""The household consumption layer (Phase 6): replenishment cycles,
predicted next-disposal dates, brand loyalty/switches, category churn, and
packaged-vs-fresh ratios — all derived from the same gated detection
history and the ConsumptionSignal rows the nightly job computes from it.

HARD GATE, enforced in exactly one place — gated_query below (and
_gated_detections_query, its common-case wrapper). Every feature function
in this module, and services/exports.py's gated report queries, build on
this one function; none of them re-implement the filter. Every computation
here excludes:
  - residents where consent_profiling is False
  - detections whose VocabularyItem has is_sensitive = True
  - detections with review_status REJECTED

Reads of already-computed ConsumptionSignal rows (get_consumption,
get_predictions) re-check the resident's *current* consent via
_resident_consents — a defense-in-depth check, not a second
implementation of the gate: it protects the narrow window between a
consent revocation and the next nightly run, when stale rows could
otherwise still be readable.
"""

import datetime as dt
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    BagType,
    Brand,
    Capture,
    CollectionSession,
    ConsumptionSignal,
    ConsumptionSignalSubjectType,
    Detection,
    ItemState,
    PickupRequest,
    Resident,
    ReviewStatus,
    VocabularyItem,
)

log = get_logger(__name__)

# Fewer than this many disposal dates and a "cycle" is noise, not signal —
# no mean/stddev/prediction/confidence is computed or reported.
MIN_OBSERVATIONS_FOR_CYCLE = 3

# A category absent for more than this multiple of its own mean cycle is
# flagged as churned (subject to the travel-gap suppression below).
CHURN_MULTIPLIER = Decimal("2.5")

_PACKAGED_CATEGORIES = {BagType.polythene.value, BagType.paper.value}
_SPOILED_STATES = {ItemState.SPOILED, ItemState.MOULDY}


def gated_query(*entities: Any) -> Select:
    """THE hard gate, parametrized on which columns/entities to select —
    every gated query in this codebase (this module's own feature
    functions, and services/exports.py's gated CSV/PDF report queries)
    builds on this one function, never a competing filter. Joins Detection
    to its capture/session/resident and (via bag_type + effective item
    name, since Detection has no FK to VocabularyItem) to the vocabulary
    entry that says whether this item is sensitive.

    Callers passing full entities (Detection, Capture, ...) get them
    automatically; callers selecting derived columns (func.count(), a
    label(), ...) must pass those directly — .select_from(Detection)
    anchors the FROM clause either way, so grouping/aggregate queries work
    the same as entity queries."""
    effective_name = func.coalesce(Detection.corrected_item_name, Detection.item_name)
    return (
        select(*entities)
        .select_from(Detection)
        .join(Capture, Detection.capture_id == Capture.id)
        .join(CollectionSession, Capture.session_id == CollectionSession.id)
        .join(Resident, CollectionSession.user_id == Resident.id)
        .outerjoin(
            VocabularyItem,
            and_(
                VocabularyItem.bag_type == Capture.bag_type,
                VocabularyItem.item_name == effective_name,
            ),
        )
        .where(
            Resident.consent_profiling.is_(True),
            Detection.review_status != ReviewStatus.rejected,
            or_(VocabularyItem.is_sensitive.is_(False), VocabularyItem.id.is_(None)),
        )
    )


def _gated_detections_query() -> Select:
    """The common case: every gated detection, alongside its capture and
    resident id."""
    return gated_query(Detection, Capture, CollectionSession.user_id.label("resident_id"))


def _resident_consents(db: Session, resident_id: uuid.UUID) -> bool:
    return bool(db.scalar(select(Resident.consent_profiling).where(Resident.id == resident_id)))


# --- Nightly computation: ConsumptionSignal -------------------------------


def _cycle_stats(
    sorted_dates: list[dt.date],
) -> tuple[Decimal | None, Decimal | None, dt.date | None, Decimal | None]:
    """(mean, stddev, predicted_next, confidence) — all None below
    MIN_OBSERVATIONS_FOR_CYCLE. confidence = 1 - stddev/mean, clamped to
    [0, 1]: a tight, regular cycle scores near 1; an erratic one near 0."""
    if len(sorted_dates) < MIN_OBSERVATIONS_FOR_CYCLE:
        return None, None, None, None

    gaps = [(sorted_dates[i] - sorted_dates[i - 1]).days for i in range(1, len(sorted_dates))]
    mean = statistics.mean(gaps)
    stddev = statistics.stdev(gaps) if len(gaps) >= 2 else 0.0
    predicted_next = sorted_dates[-1] + dt.timedelta(days=round(mean))
    confidence = max(0.0, min(1.0, 1 - (stddev / mean))) if mean > 0 else 0.0

    return (
        Decimal(str(round(mean, 2))),
        Decimal(str(round(stddev, 2))),
        predicted_next,
        Decimal(str(round(confidence, 3))),
    )


def _upsert_signal(
    db: Session,
    resident_id: uuid.UUID,
    subject_type: ConsumptionSignalSubjectType,
    subject_value: str,
    category: str,
    dates: set[dt.date],
) -> ConsumptionSignal:
    sorted_dates = sorted(dates)
    mean, stddev, predicted_next, confidence = _cycle_stats(sorted_dates)

    signal = db.scalar(
        select(ConsumptionSignal).where(
            ConsumptionSignal.resident_id == resident_id,
            ConsumptionSignal.subject_type == subject_type,
            ConsumptionSignal.subject_value == subject_value,
        )
    )
    if signal is None:
        signal = ConsumptionSignal(
            resident_id=resident_id, subject_type=subject_type, subject_value=subject_value
        )
        db.add(signal)

    signal.category = category
    signal.disposal_dates = [d.isoformat() for d in sorted_dates]
    signal.replenishment_cycle_days_mean = mean
    signal.replenishment_cycle_days_stddev = stddev
    signal.last_disposal_date = sorted_dates[-1]
    signal.predicted_next_disposal_date = predicted_next
    signal.observation_count = len(sorted_dates)
    signal.confidence = confidence
    return signal


@dataclass
class _BrandHistory:
    dates: set[dt.date] = field(default_factory=set)
    categories: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def compute_consumption_signals(db: Session) -> int:
    """Nightly job entry point. Rebuilds every ConsumptionSignal row from
    the resident's FULL gated disposal history (not a recent window — a
    replenishment cycle needs the whole timeline). Returns rows written.

    Also removes signals that should no longer exist: for a resident whose
    consent has been revoked since the last run, every one of their rows
    is deleted outright; for a still-consenting resident, any previously
    tracked subject that no longer has any gated detections (e.g. its
    vocabulary entry became sensitive, or every detection was reviewed
    away) is deleted rather than left stale.
    """
    db.execute(
        delete(ConsumptionSignal).where(
            ConsumptionSignal.resident_id.in_(
                select(Resident.id).where(Resident.consent_profiling.is_(False))
            )
        )
    )

    brand_names = {b.id: b.name for b in db.scalars(select(Brand)).all()}
    rows = db.execute(_gated_detections_query()).all()

    category_history: dict[tuple[uuid.UUID, str], set[dt.date]] = defaultdict(set)
    brand_history: dict[tuple[uuid.UUID, str], _BrandHistory] = defaultdict(_BrandHistory)

    for detection, capture, resident_id in rows:
        disposal_date = capture.captured_at.date()
        category = detection.category or capture.bag_type.value
        category_history[(resident_id, category)].add(disposal_date)

        if detection.matched_brand_id and detection.matched_brand_id in brand_names:
            brand_name = brand_names[detection.matched_brand_id]
            history = brand_history[(resident_id, brand_name)]
            history.dates.add(disposal_date)
            history.categories[category] += 1

    live_keys: dict[uuid.UUID, set[tuple[ConsumptionSignalSubjectType, str]]] = defaultdict(set)
    written = 0

    for (resident_id, category), dates in category_history.items():
        _upsert_signal(
            db, resident_id, ConsumptionSignalSubjectType.CATEGORY, category, category, dates
        )
        live_keys[resident_id].add((ConsumptionSignalSubjectType.CATEGORY, category))
        written += 1

    for (resident_id, brand_name), history in brand_history.items():
        dominant_category = max(history.categories.items(), key=lambda kv: kv[1])[0]
        _upsert_signal(
            db,
            resident_id,
            ConsumptionSignalSubjectType.BRAND,
            brand_name,
            dominant_category,
            history.dates,
        )
        live_keys[resident_id].add((ConsumptionSignalSubjectType.BRAND, brand_name))
        written += 1

    # Clean up every consenting resident who still has ConsumptionSignal
    # rows on file — including one with zero gated detections this run
    # (e.g. everything they had got reviewed away), not just those who
    # show up in live_keys.
    residents_with_signals = db.scalars(
        select(ConsumptionSignal.resident_id)
        .join(Resident, ConsumptionSignal.resident_id == Resident.id)
        .where(Resident.consent_profiling.is_(True))
        .distinct()
    ).all()
    for resident_id in residents_with_signals:
        keys = live_keys.get(resident_id, set())
        stale = db.scalars(
            select(ConsumptionSignal).where(ConsumptionSignal.resident_id == resident_id)
        ).all()
        for signal in stale:
            if (signal.subject_type, signal.subject_value) not in keys:
                db.delete(signal)

    db.commit()
    log.info("consumption_signals_computed", signals=written)
    return written


# --- Read side: per-resident consumption view -----------------------------


def _packaged_vs_fresh(db: Session, resident_id: uuid.UUID) -> tuple[float | None, float | None]:
    """(packaged_vs_fresh_ratio, spoiled_food_share).

    packaged_vs_fresh_ratio is a share in [0, 1] — the fraction of
    (packaged + fresh) trustworthy detections that are packaged — not a
    raw ratio, so it stays well-defined (no division by zero / infinity)
    when a resident has only one side of the split. None when there's
    neither.
    """
    rows = db.execute(
        _gated_detections_query().where(CollectionSession.user_id == resident_id)
    ).all()

    packaged = fresh = organic_known_state = spoiled = 0
    for detection, capture, _resident_id in rows:
        name = detection.corrected_item_name or detection.item_name
        category = detection.category or capture.bag_type.value
        if name == "unidentified_item":
            continue
        if category in _PACKAGED_CATEGORIES:
            packaged += 1
        elif category == BagType.organic.value:
            fresh += 1
            if detection.item_state is not None:
                organic_known_state += 1
                if detection.item_state in _SPOILED_STATES:
                    spoiled += 1

    total_pf = packaged + fresh
    packaged_vs_fresh_ratio = round(packaged / total_pf, 4) if total_pf else None
    spoiled_food_share = round(spoiled / organic_known_state, 4) if organic_known_state else None
    return packaged_vs_fresh_ratio, spoiled_food_share


def _brand_shares_by_category(
    brand_signals: list[ConsumptionSignal],
) -> dict[str, dict]:
    """Per category: each brand's share of observed disposals, and the
    Herfindahl index of those shares (closer to 1 = loyal to one brand;
    closer to 1/n = spread evenly across n brands) — brand loyalty."""
    by_category: dict[str, list[ConsumptionSignal]] = defaultdict(list)
    for signal in brand_signals:
        by_category[signal.category].append(signal)

    result: dict[str, dict] = {}
    for category, signals in by_category.items():
        total = sum(s.observation_count for s in signals)
        shares = []
        herfindahl = 0.0
        for s in sorted(signals, key=lambda s: -s.observation_count):
            share = s.observation_count / total if total else 0.0
            shares.append(
                {
                    "brand": s.subject_value,
                    "share": round(share, 4),
                    "observation_count": s.observation_count,
                }
            )
            herfindahl += share**2
        result[category] = {"brand_shares": shares, "herfindahl_index": round(herfindahl, 4)}
    return result


def get_consumption(db: Session, resident_id: uuid.UUID) -> dict | None:
    """The full per-resident consumption view for GET
    /profiles/{id}/consumption. None if the resident doesn't exist or
    doesn't currently consent to profiling."""
    if not _resident_consents(db, resident_id):
        return None

    signals = list(
        db.scalars(
            select(ConsumptionSignal)
            .where(ConsumptionSignal.resident_id == resident_id)
            .order_by(ConsumptionSignal.category, ConsumptionSignal.subject_value)
        )
    )
    category_signals = [
        s for s in signals if s.subject_type == ConsumptionSignalSubjectType.CATEGORY
    ]
    brand_signals = [s for s in signals if s.subject_type == ConsumptionSignalSubjectType.BRAND]

    packaged_vs_fresh_ratio, spoiled_food_share = _packaged_vs_fresh(db, resident_id)

    return {
        "resident_id": resident_id,
        "category_signals": category_signals,
        "brand_signals": brand_signals,
        "brand_loyalty": _brand_shares_by_category(brand_signals),
        "packaged_vs_fresh_ratio": packaged_vs_fresh_ratio,
        "spoiled_food_share": spoiled_food_share,
    }


def get_predictions(
    db: Session, resident_id: uuid.UUID, as_of: dt.date | None = None
) -> list[ConsumptionSignal]:
    """Due-for-replenishment items: subjects whose predicted next disposal
    date has arrived (or passed), oldest-due first. [] if the resident
    doesn't exist or doesn't currently consent to profiling."""
    if not _resident_consents(db, resident_id):
        return []
    as_of = as_of or dt.datetime.now(dt.UTC).date()
    return list(
        db.scalars(
            select(ConsumptionSignal)
            .where(
                ConsumptionSignal.resident_id == resident_id,
                ConsumptionSignal.predicted_next_disposal_date.is_not(None),
                ConsumptionSignal.predicted_next_disposal_date <= as_of,
            )
            .order_by(ConsumptionSignal.predicted_next_disposal_date.asc())
        )
    )


# --- Read side: cross-resident reports -------------------------------------


def detect_brand_switches(db: Session, days: int) -> list[dict]:
    """Brand A stops, brand B starts, same (resident, category), within
    the last `days`. Reports every stopped x started pair in a category —
    a simplification when more than one brand starts or stops in the same
    window; there's no reliable signal here to pick a single 1:1 pairing."""
    window_start = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days)

    signals = db.scalars(
        select(ConsumptionSignal)
        .join(Resident, ConsumptionSignal.resident_id == Resident.id)
        .where(
            ConsumptionSignal.subject_type == ConsumptionSignalSubjectType.BRAND,
            Resident.consent_profiling.is_(True),
        )
    ).all()

    by_group: dict[tuple[uuid.UUID, str], list[ConsumptionSignal]] = defaultdict(list)
    for signal in signals:
        by_group[(signal.resident_id, signal.category)].append(signal)

    events = []
    for (resident_id, category), group in by_group.items():
        stopped = [
            s
            for s in group
            if s.observation_count >= MIN_OBSERVATIONS_FOR_CYCLE
            and s.last_disposal_date is not None
            and s.last_disposal_date < window_start
        ]
        started = [
            s
            for s in group
            if s.disposal_dates
            and min(dt.date.fromisoformat(d) for d in s.disposal_dates) >= window_start
        ]
        for a in stopped:
            for b in started:
                if a.subject_value == b.subject_value:
                    continue
                events.append(
                    {
                        "resident_id": resident_id,
                        "category": category,
                        "brand_from": a.subject_value,
                        "brand_to": b.subject_value,
                        "brand_from_last_seen": a.last_disposal_date,
                        "brand_to_first_seen": min(
                            dt.date.fromisoformat(d) for d in b.disposal_dates
                        ),
                    }
                )
    return events


def detect_churn_risk(db: Session) -> list[dict]:
    """Category-level churn: a previously regular category absent for more
    than CHURN_MULTIPLIER times its own mean cycle.

    A travel gap is not churn: if the resident has no PickupRequest at all
    between the category's last disposal and now, the household may
    simply have been unreachable for collection — the absence says
    nothing about whether they stopped consuming that category, so the
    signal is suppressed rather than reported.
    """
    today = dt.datetime.now(dt.UTC).date()

    signals = db.scalars(
        select(ConsumptionSignal)
        .join(Resident, ConsumptionSignal.resident_id == Resident.id)
        .where(
            ConsumptionSignal.subject_type == ConsumptionSignalSubjectType.CATEGORY,
            Resident.consent_profiling.is_(True),
            ConsumptionSignal.observation_count >= MIN_OBSERVATIONS_FOR_CYCLE,
            ConsumptionSignal.replenishment_cycle_days_mean.is_not(None),
            ConsumptionSignal.last_disposal_date.is_not(None),
        )
    ).all()

    results = []
    for signal in signals:
        mean = signal.replenishment_cycle_days_mean
        assert mean is not None and signal.last_disposal_date is not None
        days_since = (today - signal.last_disposal_date).days
        if Decimal(days_since) <= CHURN_MULTIPLIER * mean:
            continue

        has_pickup = db.scalar(
            select(func.count())
            .select_from(PickupRequest)
            .where(
                PickupRequest.resident_id == signal.resident_id,
                PickupRequest.requested_for_date >= signal.last_disposal_date,
            )
        )
        if not has_pickup:
            continue  # travel gap, not churn — suppressed

        results.append(
            {
                "resident_id": signal.resident_id,
                "category": signal.category,
                "last_disposal_date": signal.last_disposal_date,
                "days_since_last_disposal": days_since,
                "expected_cycle_days": float(mean),
            }
        )
    return results
