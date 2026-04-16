"""Integration tests: PostgreSQL, FastAPI client, DB cleanup."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def worker_id() -> str:
    """``pytest-xdist`` sets ``PYTEST_XDIST_WORKER`` on workers; single-process runs use ``master``."""
    return os.environ.get("PYTEST_XDIST_WORKER", "master")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from app.libs.common.config import get_settings
from app.libs.db.database import init_db, reset_engine
from app.services.placement_service.bootstrap import ensure_default_local_execution_node


ADMIN_DATABASE_URL = "postgresql+psycopg://test:test@127.0.0.1:5432/postgres"
TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = "5432"
TEST_DB_USER = "test"
TEST_DB_PASSWORD = "test"


@pytest.fixture(autouse=True)
def _integration_internal_api_key(monkeypatch):
    """Internal notification routes require X-Internal-API-Key.

    Also disables in-process rate limiting so integration tests are not throttled
    (all test requests come from 127.0.0.1 and would quickly exhaust per-IP windows).
    """
    monkeypatch.setenv("INTERNAL_API_KEY", "integration-test-internal-key")
    monkeypatch.setenv("DEVNEST_RATE_LIMIT_ENABLED", "false")
    # Topology workspace IPs are not HTTP-reachable from the pytest host; TCP is stubbed in many suites.
    monkeypatch.setenv("DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED", "false")
    # CI sometimes sets API-tier defaults; integration runs colocated TestClient + local Docker.
    monkeypatch.setenv("DEVNEST_PROBE_ASSUME_COLOCATED_ENGINE", "true")
    get_settings.cache_clear()

    # Reset any accumulated window state from previous tests in this session.
    from app.libs.security.rate_limit import reset_all_limiters  # noqa: PLC0415
    reset_all_limiters()

    yield

    get_settings.cache_clear()
    reset_all_limiters()


@pytest.fixture(scope="session")
def worker_database_url(worker_id: str) -> str:
    """
    Give each pytest-xdist worker its own database.

    Examples:
      master -> devnest_test
      gw0    -> devnest_test_gw0
      gw1    -> devnest_test_gw1
    """
    db_name = "devnest_test" if worker_id == "master" else f"devnest_test_{worker_id}"

    admin_engine = create_engine(
        ADMIN_DATABASE_URL,
        isolation_level="AUTOCOMMIT",
    )

    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    admin_engine.dispose()

    database_url = (
        f"postgresql+psycopg://{TEST_DB_USER}:{TEST_DB_PASSWORD}"
        f"@{TEST_DB_HOST}:{TEST_DB_PORT}/{db_name}"
    )

    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    reset_engine()

    yield database_url

    admin_engine = create_engine(
        ADMIN_DATABASE_URL,
        isolation_level="AUTOCOMMIT",
    )
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def test_engine(worker_database_url: str) -> Engine:
    """
    Create the app engine after DATABASE_URL has been set for this worker,
    then initialize schema once for this worker's isolated database.
    """
    from app.libs.db.database import get_engine

    get_settings.cache_clear()
    reset_engine()

    engine = get_engine()
    init_db()

    yield engine

    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(test_engine: Engine):
    """
    Clean only this worker's database before each test.
    Safe because each worker has its own isolated DB.
    """
    with test_engine.connect() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(
                text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
            )
        conn.commit()

    with Session(test_engine) as seed_session:
        ensure_default_local_execution_node(seed_session)
        seed_session.commit()

    yield


@pytest.fixture
def db_session(test_engine: Engine):
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def client(test_engine: Engine):
    from app.main import app
    from app.services.auth_service.api.dependencies import get_db

    def override_get_db():
        db = Session(test_engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
