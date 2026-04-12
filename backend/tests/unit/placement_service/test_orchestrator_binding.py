"""Unit tests: ``resolve_orchestrator_placement`` (SQLite, full metadata)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.auth_service.models import UserAuth
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.placement_service.orchestrator_binding import resolve_orchestrator_placement
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceStatus,
)


@pytest.fixture
def bind_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_user(session: Session) -> int:
    u = UserAuth(username="u1", email="u1@example.com", password_hash="x")
    session.add(u)
    session.commit()
    session.refresh(u)
    assert u.user_auth_id is not None
    return u.user_auth_id


def _seed_workspace_and_job(
    session: Session,
    *,
    owner_id: int,
    job_type: str,
) -> tuple[Workspace, WorkspaceJob]:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="ws1",
        description="",
        owner_user_id=owner_id,
        status=WorkspaceStatus.CREATING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=job_type,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_id,
        requested_config_version=1,
        attempt=0,
    )
    session.add(job)
    session.commit()
    session.refresh(ws)
    session.refresh(job)
    assert ws.workspace_id is not None and job.workspace_job_id is not None
    return ws, job


def _add_node(session: Session, *, key: str, alloc_cpu: float = 4.0, alloc_mem: int = 8192) -> None:
    session.add(
        ExecutionNode(
            node_key=key,
            name=key,
            provider_type=ExecutionNodeProviderType.LOCAL.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=4.0,
            total_memory_mb=8192,
            allocatable_cpu=alloc_cpu,
            allocatable_memory_mb=alloc_mem,
        )
    )
    session.commit()


def test_create_selects_highest_capacity_node(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session, key="small", alloc_cpu=2.0)
        _add_node(session, key="big", alloc_cpu=8.0)
        ws, job = _seed_workspace_and_job(session, owner_id=uid, job_type=WorkspaceJobType.CREATE.value)
        node_key, tid = resolve_orchestrator_placement(session, ws, job)
        assert node_key == "big"
        assert tid == 1


def test_stop_reuses_runtime_node_and_topology(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session, key="n1", alloc_cpu=4.0)
        ws, job = _seed_workspace_and_job(session, owner_id=uid, job_type=WorkspaceJobType.STOP.value)
        wid = ws.workspace_id
        assert wid is not None
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                container_id="c1",
                container_state="running",
                topology_id=42,
                internal_endpoint="http://10.0.0.1:8080",
                config_version=1,
            )
        )
        session.commit()
        session.refresh(ws)
        session.refresh(job)
        node_key, tid = resolve_orchestrator_placement(session, ws, job)
        assert node_key == "n1"
        assert tid == 42


def test_fallback_to_env_when_no_runtime_and_not_placement_job(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session, key="only", alloc_cpu=4.0)
        ws, job = _seed_workspace_and_job(
            session,
            owner_id=uid,
            job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        )
        with patch.dict(
            "os.environ",
            {"DEVNEST_NODE_ID": "env-node", "DEVNEST_TOPOLOGY_ID": "7"},
            clear=False,
        ):
            node_key, tid = resolve_orchestrator_placement(session, ws, job)
        assert node_key == "env-node"
        assert tid == 7
