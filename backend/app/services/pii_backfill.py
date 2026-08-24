"""One-time backfill: encrypt existing plaintext Resident PII in place and
populate phone_index (Phase 7, migration 0010).

Uses raw SQL via a Core Connection, not the Resident ORM model. By the time
this runs, that model's column types already assume ciphertext (see
models/accounts.py) — reading existing plaintext rows through it would try
to Fernet-decrypt plaintext and raise. Same reasoning, same shape, as
services/inference_backfill.py's exception to the "migrations don't import
app code" convention.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.encryption import blind_index, decrypt_value, encrypt_value


def encrypt_existing_residents(conn: Connection) -> int:
    rows = conn.execute(text("SELECT id, name, phone, address FROM users")).all()
    for row in rows:
        conn.execute(
            text(
                "UPDATE users SET name = :name, phone = :phone_ciphertext, "
                "phone_index = :phone_idx, address = :address WHERE id = :id"
            ),
            {
                "id": row.id,
                "name": encrypt_value(row.name),
                "phone_ciphertext": encrypt_value(row.phone),
                "phone_idx": blind_index(row.phone),
                "address": encrypt_value(row.address),
            },
        )
    return len(rows)


def decrypt_existing_residents(conn: Connection) -> int:
    """Reverse of encrypt_existing_residents — used by the migration's
    downgrade path. Assumes every row currently holds ciphertext."""
    rows = conn.execute(text("SELECT id, name, phone, address FROM users")).all()
    for row in rows:
        conn.execute(
            text(
                "UPDATE users SET name = :name, phone = :phone, address = :address WHERE id = :id"
            ),
            {
                "id": row.id,
                "name": decrypt_value(row.name),
                "phone": decrypt_value(row.phone),
                "address": decrypt_value(row.address),
            },
        )
    return len(rows)
