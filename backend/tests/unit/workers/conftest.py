"""SQLite engine + owner user for workspace job worker unit tests (no PostgreSQL)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import (  # noqa: F401 — register metadata
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceRuntime,
)


@pytest.fixture
def workspace_job_worker_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def owner_user_id(workspace_job_worker_engine: Engine) -> int:
    with Session(workspace_job_worker_engine) as session:
        user = UserAuth(
            username="job_worker_owner",
            email="job_worker_owner@example.com",
            password_hash="unused-hash",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.user_auth_id is not None
        return user.user_auth_id
