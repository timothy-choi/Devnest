"""SQLite engine + owner user for observability metric tests (same pattern as workspace unit tests).

Nested ``pytest_plugins`` is forbidden by pytest 8+; fixtures are defined locally instead of
re-exporting ``tests.unit.workspace.conftest``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.placement_service.bootstrap import ensure_default_local_execution_node
from app.services.placement_service.models import ExecutionNode  # noqa: F401 — register metadata
from app.services.workspace_service.models import (  # noqa: F401 — register metadata before create_all
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceRuntime,
    WorkspaceSession,
)


@pytest.fixture
def workspace_unit_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_default_local_execution_node(session)
        session.commit()
    return engine


@pytest.fixture
def owner_user_id(workspace_unit_engine: Engine) -> int:
    with Session(workspace_unit_engine) as session:
        user = UserAuth(
            username="obs_unit_owner",
            email="obs_unit_owner@example.com",
            password_hash="unused-hash",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.user_auth_id is not None
        return user.user_auth_id
