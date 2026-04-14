"""Durable cleanup queue: enqueue and reconcile-time processing."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.services.cleanup_service import (
    CLEANUP_SCOPE_BRINGUP_ROLLBACK,
    ensure_durable_cleanup_task,
    process_durable_cleanup_tasks_for_workspace,
)
from app.services.auth_service.models import UserAuth
from app.services.orchestrator_service.results import WorkspaceStopResult
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceCleanupTask,
    WorkspaceRuntime,
    WorkspaceStatus,
)
from app.services.workspace_service.models.enums import WorkspaceCleanupTaskStatus, WorkspaceRuntimeHealthStatus


@pytest.fixture
def engine() -> Engine:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def _seed_workspace(session: Session) -> int:
    u = UserAuth(username="cu", email="cu@example.com", password_hash="x")
    session.add(u)
    session.commit()
    session.refresh(u)
    uid = u.user_auth_id
    assert uid is not None
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="c1",
        description="",
        owner_user_id=uid,
        status=WorkspaceStatus.ERROR.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def test_ensure_cleanup_task_idempotent(engine: Engine) -> None:
    with Session(engine) as session:
        wid = _seed_workspace(session)
        ensure_durable_cleanup_task(session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK, detail=["a"])
        ensure_durable_cleanup_task(session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK, detail=["b"])
        session.commit()
        rows = session.exec(select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid)).all()
        assert len(rows) == 1
        assert rows[0].status == WorkspaceCleanupTaskStatus.PENDING.value


def test_process_cleanup_calls_stop_and_succeeds(engine: Engine) -> None:
    orch = MagicMock()
    orch.stop_workspace_runtime.return_value = WorkspaceStopResult(
        workspace_id="1",
        success=True,
        container_id="c1",
        container_state="stopped",
        topology_detached=True,
        issues=None,
    )

    with Session(engine) as session:
        wid = _seed_workspace(session)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="nk",
                topology_id=3,
                container_id="c1",
                health_status=WorkspaceRuntimeHealthStatus.CLEANUP_REQUIRED.value,
            ),
        )
        ensure_durable_cleanup_task(session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK, detail=["rollback"])
        session.commit()

    with Session(engine) as session:
        ws = session.get(Workspace, wid)
        assert ws is not None
        n = process_durable_cleanup_tasks_for_workspace(session, orch, ws, correlation_id="t1")
        session.commit()
        assert n == 1

    orch.stop_workspace_runtime.assert_called_once()
    call_kw = orch.stop_workspace_runtime.call_args.kwargs
    assert call_kw["release_ip_lease"] is True
    assert call_kw["container_id"] == "c1"

    with Session(engine) as session:
        task = session.exec(select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid)).first()
        assert task is not None
        assert task.status == WorkspaceCleanupTaskStatus.SUCCEEDED.value
        rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
        assert rt is not None
        assert rt.health_status == WorkspaceRuntimeHealthStatus.UNKNOWN.value
