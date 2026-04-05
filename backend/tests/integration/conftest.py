"""Integration tests: PostgreSQL, FastAPI client, DB cleanup."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel

from app.libs.db.database import init_db


@pytest.fixture(scope="session")
def test_engine():
    from app.libs.db.database import get_engine

    engine = get_engine()
    init_db()
    yield engine


@pytest.fixture(autouse=True)
def _clean_tables(test_engine):
    def truncate() -> None:
        with test_engine.connect() as conn:
            for table in reversed(SQLModel.metadata.sorted_tables):
                conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
            conn.commit()

    truncate()
    yield


@pytest.fixture
def db_session(test_engine):
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def client(test_engine):
    from app.services.auth_service.api.dependencies import get_db
    from app.services.auth_service.api.main import app

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
