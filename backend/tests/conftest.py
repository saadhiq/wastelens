"""Test fixtures.

Unit tests (security, health/live) run with no infrastructure. DB-backed tests
use TEST_DATABASE_URL (defaults to the compose Postgres on localhost) and are
skipped automatically when no database is reachable — CI provides one via a
postgres service container.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Base, StaffAccount, StaffRole

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://wastelens:change-me@localhost:5432/wastelens_test",
)


def _db_available() -> bool:
    try:
        engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="test database not reachable (set TEST_DATABASE_URL)"
)


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    # Route commits to a savepoint so each test rolls back cleanly.
    session.begin_nested()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_engine):
    """TestClient wired to the test database (fresh session per request)."""
    TestSession = sessionmaker(bind=db_engine, expire_on_commit=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def bare_client():
    """TestClient with no DB override — for endpoints that don't touch the DB."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def admin_account(db_engine):
    """A persisted admin account (unique email per test run)."""
    TestSession = sessionmaker(bind=db_engine)
    db = TestSession()
    email = f"admin-{uuid.uuid4().hex[:8]}@wastelens-test.io"
    account = StaffAccount(
        email=email,
        full_name="Test Admin",
        hashed_password=hash_password("admin-pass-123"),
        role=StaffRole.admin,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    db.close()
    return {"email": email, "password": "admin-pass-123", "id": str(account.id)}


@pytest.fixture()
def analyst_account(db_engine):
    TestSession = sessionmaker(bind=db_engine)
    db = TestSession()
    email = f"analyst-{uuid.uuid4().hex[:8]}@wastelens-test.io"
    account = StaffAccount(
        email=email,
        full_name="Test Analyst",
        hashed_password=hash_password("analyst-pass-123"),
        role=StaffRole.analyst,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    db.close()
    return {"email": email, "password": "analyst-pass-123", "id": str(account.id)}


@pytest.fixture()
def reviewer_account(db_engine):
    TestSession = sessionmaker(bind=db_engine)
    db = TestSession()
    email = f"reviewer-{uuid.uuid4().hex[:8]}@wastelens-test.io"
    account = StaffAccount(
        email=email,
        full_name="Test Reviewer",
        hashed_password=hash_password("reviewer-pass-123"),
        role=StaffRole.reviewer,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    db.close()
    return {"email": email, "password": "reviewer-pass-123", "id": str(account.id)}


@pytest.fixture()
def station_operator_account(db_engine):
    TestSession = sessionmaker(bind=db_engine)
    db = TestSession()
    email = f"station-{uuid.uuid4().hex[:8]}@wastelens-test.io"
    account = StaffAccount(
        email=email,
        full_name="Test Station Operator",
        hashed_password=hash_password("station-pass-123"),
        role=StaffRole.station_operator,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    db.close()
    return {"email": email, "password": "station-pass-123", "id": str(account.id)}


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
