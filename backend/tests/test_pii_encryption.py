"""Phase 7: column-level PII encryption on Resident (closes DECISIONS.md
#2). Fernet/blind-index unit tests, ORM transparency (ciphertext at rest,
plaintext through the model), and the backfill service the migration uses."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.encryption import blind_index, decrypt_value, encrypt_value
from app.models import Resident
from app.services.pii_backfill import decrypt_existing_residents, encrypt_existing_residents
from tests.conftest import login, requires_db

pytestmark = requires_db


def _digit_suffix(n: int = 8) -> str:
    """A random all-digit string — unlike uuid4().hex (which can contain
    a-f), this always passes the API's digits-only phone validator."""
    return str(uuid.uuid4().int)[:n]


@pytest.fixture()
def db(db_engine):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


class TestEncryptionPrimitives:
    def test_roundtrip(self):
        plaintext = "a secret value"
        ciphertext = encrypt_value(plaintext)
        assert ciphertext != plaintext
        assert decrypt_value(ciphertext) == plaintext

    def test_encryption_is_non_deterministic(self):
        a = encrypt_value("+94771234567")
        b = encrypt_value("+94771234567")
        assert a != b  # different IV each time
        assert decrypt_value(a) == decrypt_value(b) == "+94771234567"

    def test_blind_index_is_deterministic(self):
        assert blind_index("+94771234567") == blind_index("+94771234567")

    def test_blind_index_differs_for_different_input(self):
        assert blind_index("+94771234567") != blind_index("+94771234568")


class TestResidentModelTransparency:
    def test_ciphertext_at_rest_plaintext_through_orm(self, db):
        suffix = uuid.uuid4().hex[:8]
        phone = f"+9471{suffix[:7]}"
        resident = Resident(name="Ciphertext Test", phone=phone, address="123 Test Rd")
        db.add(resident)
        db.commit()

        raw = db.execute(
            text("SELECT name, phone, phone_index FROM users WHERE id = :id"),
            {"id": resident.id},
        ).one()
        assert raw.name != "Ciphertext Test"
        assert raw.phone != phone
        assert raw.phone_index == blind_index(phone)

        db.expire_all()
        reloaded = db.get(Resident, resident.id)
        assert reloaded.name == "Ciphertext Test"
        assert reloaded.phone == phone
        assert reloaded.address == "123 Test Rd"

    def test_updating_phone_keeps_index_in_sync(self, db):
        suffix = uuid.uuid4().hex[:8]
        resident = Resident(name="Reindex Test", phone=f"+9472{suffix[:7]}", address="x")
        db.add(resident)
        db.commit()

        new_phone = f"+9473{suffix[:7]}"
        resident.phone = new_phone
        db.commit()
        db.expire_all()

        reloaded = db.get(Resident, resident.id)
        assert reloaded.phone == new_phone
        assert reloaded.phone_index == blind_index(new_phone)


class TestBackfillService:
    def test_encrypt_then_decrypt_restores_original(self, db):
        # Insert plaintext directly (bypassing the ORM's own encryption) to
        # simulate the pre-migration state the real backfill runs against.
        suffix = uuid.uuid4().hex[:8]
        resident_id = uuid.uuid4()
        name, phone, address = f"Raw-{suffix}", f"+9474{suffix[:7]}", "456 Raw Ave"
        db.execute(
            text(
                "INSERT INTO users "
                "(id, name, phone, phone_index, address, "
                "consent_operational, consent_profiling, status) "
                "VALUES "
                "(:id, :name, :phone_col, :phone_idx_col, :address, true, false, 'ACTIVE')"
            ),
            {
                "id": resident_id,
                "name": name,
                "phone_col": phone,
                "phone_idx_col": phone,
                "address": address,
            },
        )
        db.commit()

        encrypted = encrypt_existing_residents(db.connection())
        db.commit()
        assert encrypted >= 1

        raw = db.execute(
            text("SELECT name, phone, phone_index FROM users WHERE id = :id"), {"id": resident_id}
        ).one()
        assert raw.name != name
        assert raw.phone != phone
        assert raw.phone_index == blind_index(phone)

        decrypted = decrypt_existing_residents(db.connection())
        db.commit()
        assert decrypted >= 1

        restored = db.execute(
            text("SELECT name, phone, address FROM users WHERE id = :id"), {"id": resident_id}
        ).one()
        assert restored.name == name
        assert restored.phone == phone
        assert restored.address == address


class TestPhoneUniquenessSurvivesEncryption:
    def test_duplicate_phone_rejected_on_create(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        phone = f"+9475{_digit_suffix(7)}"
        body = {"name": "First", "phone": phone, "address": "x"}
        first = client.post("/api/v1/users", headers=headers, json=body)
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/v1/users",
            headers=headers,
            json={"name": "Second", "phone": phone, "address": "y"},
        )
        assert second.status_code == 409

    def test_duplicate_phone_rejected_on_update(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        phone_a = f"+9476{_digit_suffix(7)}"
        phone_b = f"+9477{_digit_suffix(7)}"
        a = client.post(
            "/api/v1/users", headers=headers, json={"name": "A", "phone": phone_a, "address": "x"}
        ).json()
        client.post(
            "/api/v1/users", headers=headers, json={"name": "B", "phone": phone_b, "address": "y"}
        )

        resp = client.patch(f"/api/v1/users/{a['id']}", headers=headers, json={"phone": phone_b})
        assert resp.status_code == 409

    def test_lookup_by_phone_works_after_encryption(self, client, admin_account):
        headers = login(client, admin_account["email"], admin_account["password"])
        phone = f"+9478{_digit_suffix(7)}"
        client.post(
            "/api/v1/users",
            headers=headers,
            json={"name": "Findable", "phone": phone, "address": "x"},
        )
        resp = client.get(f"/api/v1/users/by-phone/{phone}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["phone"] == phone
        assert resp.json()["name"] == "Findable"
