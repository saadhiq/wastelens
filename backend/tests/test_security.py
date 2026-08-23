"""Unit tests for password hashing and JWT handling — no infrastructure needed."""

import jwt as pyjwt
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret!")
    assert hashed != "s3cret!"
    assert verify_password("s3cret!", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_tolerates_garbage_hash():
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_access_token_roundtrip():
    token = create_access_token("subject-1")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "subject-1"


def test_refresh_token_not_accepted_as_access():
    token = create_refresh_token("subject-1")
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(token, expected_type="access")


def test_tampered_token_rejected():
    token = create_access_token("subject-1")
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(token + "x", expected_type="access")
