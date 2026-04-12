"""SQLite engine for infrastructure unit tests (routes + lifecycle)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

# Register all models for metadata.create_all
from app.services.auth_service.models import UserAuth  # noqa: F401
from app.services.placement_service.models import ExecutionNode  # noqa: F401
from app.services.workspace_service.models import (  # noqa: F401
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceRuntime,
)


@pytest.fixture
def infrastructure_unit_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine
