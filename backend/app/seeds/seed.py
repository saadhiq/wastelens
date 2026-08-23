"""Idempotent seed script: item vocabulary, starter brand list, and a bootstrap
admin account (from BOOTSTRAP_ADMIN_* env vars, only if no admin exists).

Run inside the api container:  python -m app.seeds.seed
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import BagType, Brand, StaffAccount, StaffRole, VocabularyItem

log = get_logger(__name__)

VOCABULARY: dict[BagType, list[str]] = {
    BagType.organic: [
        "beetroot_peel",
        "cabbage_leaf",
        "beans_ends",
        "ladies_finger_ends",
        "onion_peel",
        "potato_peel",
        "banana_peel",
        "tomato",
        "carrot_top",
        "coconut_shell",
        "egg_shell",
        "rice_leftover",
        "tea_leaves",
        "unidentified_item",
    ],
    BagType.polythene: [
        "chips_packet",
        "biscuit_wrapper",
        "milk_packet",
        "plastic_bag",
        "bottle",
        "yogurt_cup",
        "shampoo_sachet",
        "unidentified_item",
    ],
    BagType.paper: [
        "biscuit_box",
        "newspaper",
        "cardboard_carton",
        "paper_bag",
        "egg_tray",
        "tissue",
        "magazine",
        "unidentified_item",
    ],
    BagType.general: [
        "cloth",
        "foil",
        "broken_item",
        "mixed_scrap",
        "unidentified_item",
    ],
}

# Small editable starter list — admins manage this via CRUD in Phase 2.
BRANDS: list[dict] = [
    {"name": "Munchee", "aliases": ["munchee biscuits"], "category": "biscuits"},
    {"name": "Maliban", "aliases": ["maliban biscuits"], "category": "biscuits"},
    {"name": "Anchor", "aliases": ["anchor milk"], "category": "dairy"},
    {"name": "Highland", "aliases": ["highland milk"], "category": "dairy"},
    {"name": "Kist", "aliases": [], "category": "processed food"},
    {"name": "Elephant House", "aliases": ["eh"], "category": "beverages"},
    {"name": "Sunlight", "aliases": [], "category": "household"},
    {"name": "Signal", "aliases": [], "category": "personal care"},
]


def _display_name(item_name: str) -> str:
    return item_name.replace("_", " ").capitalize()


def seed_vocabulary(db: Session) -> int:
    added = 0
    for bag_type, items in VOCABULARY.items():
        for item_name in items:
            exists = db.scalar(
                select(VocabularyItem).where(
                    VocabularyItem.bag_type == bag_type,
                    VocabularyItem.item_name == item_name,
                )
            )
            if exists is None:
                db.add(
                    VocabularyItem(
                        bag_type=bag_type,
                        item_name=item_name,
                        display_name=_display_name(item_name),
                    )
                )
                added += 1
    return added


def seed_brands(db: Session) -> int:
    added = 0
    for entry in BRANDS:
        exists = db.scalar(select(Brand).where(Brand.name == entry["name"]))
        if exists is None:
            db.add(Brand(**entry))
            added += 1
    return added


def seed_admin(db: Session) -> bool:
    settings = get_settings()
    has_admin = db.scalar(select(StaffAccount).where(StaffAccount.role == StaffRole.admin))
    if has_admin is not None:
        return False
    db.add(
        StaffAccount(
            email=settings.bootstrap_admin_email,
            full_name="Bootstrap Admin",
            hashed_password=hash_password(settings.bootstrap_admin_password),
            role=StaffRole.admin,
        )
    )
    return True


def main() -> None:
    configure_logging()
    db = SessionLocal()
    try:
        vocab_added = seed_vocabulary(db)
        brands_added = seed_brands(db)
        admin_created = seed_admin(db)
        db.commit()
        log.info(
            "seed_complete",
            vocabulary_added=vocab_added,
            brands_added=brands_added,
            admin_created=admin_created,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
