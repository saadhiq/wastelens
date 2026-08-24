"""Column-level PII encryption (Phase 7 — closes DECISIONS.md #2).

Two primitives:
  - encrypt_value/decrypt_value: Fernet (AES-128-CBC + HMAC), non-deterministic
    — the same plaintext encrypts to a different ciphertext every time. Used
    for Resident.name/address/phone, transparently, via EncryptedString below.
  - blind_index: HMAC-SHA256 of a normalized value, deterministic — the same
    input always hashes the same. Used ONLY for Resident.phone_index, so the
    unique-phone constraint and exact-match lookups still work without the
    stored ciphertext itself being deterministic (which would leak equality
    across every row, not just support lookups). See DECISIONS.md.
"""

import hashlib
import hmac

from cryptography.fernet import Fernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().pii_encryption_key.encode())


def encrypt_value(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def blind_index(value: str) -> str:
    """Deterministic HMAC-SHA256 hex digest, keyed by a pepper separate from
    the encryption key. Used to make an encrypted column queryable by exact
    match without the stored ciphertext itself being deterministic."""
    key = get_settings().pii_blind_index_key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


class EncryptedString(TypeDecorator):
    """A column type that stores ciphertext but behaves like a plain string
    to every ORM caller — encryption/decryption happens transparently at the
    bind/result boundary. Backed by Text (Fernet tokens run considerably
    longer than their plaintext, so any fixed-length String would truncate).

    Not queryable by value at the SQL level (WHERE this_column = 'x' will
    essentially never match, since Fernet output isn't deterministic) — that
    is deliberate, not a bug. A column that needs exact-match queries (like
    Resident.phone) pairs this with a separate blind_index column instead of
    using EncryptedString for the queryable half.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        if value is None:
            return None
        return encrypt_value(value)

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        if value is None:
            return None
        return decrypt_value(value)
