"""Phase 7: column-level encryption for Resident PII (name/phone/address),
closing DECISIONS.md #2. Widens the three columns to text (Fernet
ciphertext runs longer than the original bounded varchars), encrypts every
existing row's values in place, adds phone_index (a deterministic HMAC
blind index — see app/core/encryption.py) to replace the old plaintext
unique-phone index, since ciphertext itself can't be looked up by exact
match.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.services.pii_backfill import encrypt_existing_residents

    op.alter_column("users", "name", type_=sa.Text())
    op.alter_column("users", "phone", type_=sa.Text())
    op.alter_column("users", "address", type_=sa.Text())
    op.add_column("users", sa.Column("phone_index", sa.String(64), nullable=True))

    conn = op.get_bind()
    encrypted = encrypt_existing_residents(conn)

    op.drop_index("ix_users_phone", table_name="users")
    op.alter_column("users", "phone_index", nullable=False)
    op.create_index("ix_users_phone_index", "users", ["phone_index"], unique=True)

    print(f"0010: encrypted {encrypted} resident row(s)")  # noqa: T201


def downgrade() -> None:
    from app.services.pii_backfill import decrypt_existing_residents

    op.drop_index("ix_users_phone_index", table_name="users")

    conn = op.get_bind()
    decrypted = decrypt_existing_residents(conn)

    op.drop_column("users", "phone_index")
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.alter_column("users", "name", type_=sa.String(200))
    op.alter_column("users", "phone", type_=sa.String(20))
    op.alter_column("users", "address", type_=sa.String(500))

    print(f"0010 downgrade: decrypted {decrypted} resident row(s)")  # noqa: T201
