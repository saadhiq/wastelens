"""Collection-session creation for the collector's doorstep flow (Phase 4).

POST /sessions accepts the collector, vehicle, route, GPS, and every bag
collected in ONE request — the collector is standing at a door, often
offline, and can't afford a multi-step wizard of separate calls.

Bag identity resolution (see DECISIONS.md): a bag in the nested request is
matched to an existing Bag row by tag_id if given and found; otherwise a new
Bag is created (using the given tag_id, or a server-generated one if the
collector had no working QR/tag to scan). This lets pre-registered bags and
ad-hoc doorstep bags go through the same call.
"""

import datetime as dt
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bag,
    BagStatus,
    CollectionSession,
    Collector,
    PickupRequest,
    PickupStatus,
    Resident,
    StaffAccount,
)
from app.schemas.captures import SessionCreate
from app.services.audit import record


def _resolve_bag(db: Session, *, user_id: uuid.UUID, bag_input) -> Bag:
    bag = None
    if bag_input.tag_id:
        bag = db.scalar(select(Bag).where(Bag.tag_id == bag_input.tag_id))

    if bag is not None:
        if bag.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tag {bag_input.tag_id!r} is registered to a different household",
            )
        if bag.bag_type != bag_input.bag_type:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Tag {bag_input.tag_id!r} is registered as {bag.bag_type.value}, "
                    f"not {bag_input.bag_type.value}"
                ),
            )
    else:
        tag_id = bag_input.tag_id or f"auto-{uuid.uuid4().hex[:12]}"
        bag = Bag(user_id=user_id, bag_type=bag_input.bag_type, tag_id=tag_id)
        db.add(bag)
        db.flush()

    return bag


def create_session_with_bags(
    db: Session, *, body: SessionCreate, account: StaffAccount
) -> CollectionSession:
    if db.get(Resident, body.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resident not found")

    if body.collector_id is not None and db.get(Collector, body.collector_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collector not found")

    pickup: PickupRequest | None = None
    if body.pickup_request_id is not None:
        pickup = db.get(PickupRequest, body.pickup_request_id)
        if pickup is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pickup not found")

    session = CollectionSession(
        user_id=body.user_id,
        collector_id=body.collector_id,
        vehicle_code=body.vehicle_code,
        route_code=body.route_code,
        gps_latitude=body.gps_latitude,
        gps_longitude=body.gps_longitude,
        notes=body.notes,
    )
    db.add(session)
    db.flush()

    for bag_input in body.bags:
        bag = _resolve_bag(db, user_id=body.user_id, bag_input=bag_input)
        bag.collection_session_id = session.id
        bag.status = BagStatus.collected
        if bag_input.gross_weight_kg is not None:
            bag.gross_weight_kg = bag_input.gross_weight_kg
        if bag_input.tare_weight_kg is not None:
            bag.tare_weight_kg = bag_input.tare_weight_kg
        if bag_input.bag_condition is not None:
            bag.bag_condition = bag_input.bag_condition

    if pickup is not None:
        pickup.status = PickupStatus.COMPLETED
        pickup.collection_session_id = session.id

    record(
        db,
        actor_id=account.id,
        action="session.create",
        entity_type="collection_session",
        entity_id=str(session.id),
        detail={
            "bag_count": len(body.bags),
            "pickup_request_id": str(body.pickup_request_id or ""),
        },
    )
    db.commit()
    db.refresh(session)
    return session


def mark_session_arrived(
    db: Session,
    *,
    session: CollectionSession,
    arrived_at: dt.datetime | None,
    account: StaffAccount,
) -> CollectionSession:
    session.warehouse_arrival_at = arrived_at or dt.datetime.now(dt.UTC)
    record(
        db,
        actor_id=account.id,
        action="session.arrive",
        entity_type="collection_session",
        entity_id=str(session.id),
    )
    db.commit()
    db.refresh(session)
    return session
