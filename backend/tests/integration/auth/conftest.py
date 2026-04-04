"""
Auth integration test fixtures.

Sets DATABASE_URL before any app import so `app.libs.db.database` can load without Postgres.
Tests use a dedicated in-memory SQLite engine and override FastAPI `get_db` so requests never
use the module-level engine for persistence.
"""

from __future__ import annotations

import os

# Ensure settings/database modules see a SQLite URL if env is unset (import-time engine creation).
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import OAuth, Token, UserAuth  # noqa: F401 — metadata


@pytest.fixture
def test_engine():
    """Fresh in-memory DB per test; all auth models registered on metadata."""
    # StaticPool: one connection so :memory: is shared across Session + TestClient threads.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Direct session for assertions outside the HTTP layer (same engine as API)."""
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def client(test_engine, monkeypatch):
    """
    TestClient with `get_db` overridden to use `test_engine`.
    Skips lifespan `init_db()` so tables are not created on the production module engine.
    """
    monkeypatch.setattr("app.services.auth_service.api.main.init_db", lambda: None)

    from app.services.auth_service.api.dependencies import get_db
    from app.services.auth_service.api.main import app

    def override_get_db():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
