"""Shared SQLite engine + owner user for workspace unit tests (no PostgreSQL)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import (  # noqa: F401 — register metadata before create_all
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceRuntime,
)


@pytest.fixture
def workspace_unit_engine() -> Engine:
    # StaticPool: one connection so in-memory SQLite is shared across Sessions (route tests + TestClient).
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def owner_user_id(workspace_unit_engine: Engine) -> int:
    with Session(workspace_unit_engine) as session:
        user = UserAuth(
            username="ws_unit_owner",
            email="ws_unit_owner@example.com",
            password_hash="unused-hash",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.user_auth_id is not None
        return user.user_auth_id
