"""Worker/reconcile-independent cleanup drain (durable ledger)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.services.auth_service.models import UserAuth
from app.services.cleanup_service import (
    CLEANUP_SCOPE_BRINGUP_ROLLBACK,
    drain_pending_cleanup_tasks,
    ensure_durable_cleanup_task,
)
from app.services.orchestrator_service.results import WorkspaceStopResult
from app.services.workspace_service.models import Workspace, WorkspaceCleanupTask, WorkspaceRuntime, WorkspaceStatus
from app.services.workspace_service.models.enums import WorkspaceCleanupTaskStatus


@pytest.fixture
def engine() -> Engine:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def _seed_workspace(session: Session) -> int:
    u = UserAuth(username="drain_u", email="drain_u@example.com", password_hash="x")
    session.add(u)
    session.commit()
    session.refresh(u)
    uid = u.user_auth_id
    assert uid is not None
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="drain_ws",
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


def test_drain_marks_deferred_when_runtime_incomplete(engine: Engine) -> None:
    with Session(engine) as session:
        wid = _seed_workspace(session)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="nk",
                topology_id=None,
                container_id="c1",
            ),
        )
        ensure_durable_cleanup_task(session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK, detail=["x"])
        session.commit()

    with Session(engine) as session:
        assert drain_pending_cleanup_tasks(session, limit_workspaces=4) == 0
        session.commit()
        task = session.exec(
            select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid),
        ).first()
        assert task is not None
        assert task.status == WorkspaceCleanupTaskStatus.PENDING.value
        assert task.detail is not None
        assert "runtime_placement_incomplete" in task.detail


def test_drain_deferred_idempotent_second_tick(engine: Engine) -> None:
    with Session(engine) as session:
        wid = _seed_workspace(session)
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="nk",
                topology_id=None,
                container_id="c1",
            ),
        )
        ensure_durable_cleanup_task(session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK)
        session.commit()

    with Session(engine) as session:
        drain_pending_cleanup_tasks(session, limit_workspaces=4)
        session.commit()
        t1 = session.exec(
            select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid),
        ).first()
        d1 = t1.detail if t1 else None

    with Session(engine) as session:
        drain_pending_cleanup_tasks(session, limit_workspaces=4)
        session.commit()
        t2 = session.exec(
            select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid),
        ).first()
        assert t2 is not None
        assert t2.detail == d1


def test_drain_processes_when_runtime_complete(engine: Engine) -> None:
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
            ),
        )
        ensure_durable_cleanup_task(session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK)
        session.commit()

    with Session(engine) as session:
        with patch(
            "app.services.cleanup_service.service.build_default_orchestrator_for_session",
            return_value=orch,
        ):
            n = drain_pending_cleanup_tasks(session, limit_workspaces=4)
        session.commit()
        assert n == 1
        task = session.exec(
            select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid),
        ).first()
        assert task is not None
        assert task.status == WorkspaceCleanupTaskStatus.SUCCEEDED.value
