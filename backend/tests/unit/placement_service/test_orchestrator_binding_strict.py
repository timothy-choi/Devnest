"""Authoritative placement when env fallback is disabled (production-like)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.libs.topology.models import Topology  # noqa: F401 — register metadata for create_all
from app.services.auth_service.models import UserAuth
from app.services.placement_service.errors import AuthoritativePlacementError, InvalidPlacementParametersError
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
    u = UserAuth(username="u_strict", email="u_strict@example.com", password_hash="x")
    session.add(u)
    session.commit()
    session.refresh(u)
    assert u.user_auth_id is not None
    return u.user_auth_id


def _seed_topology(session: Session, topology_id: int) -> None:
    if session.get(Topology, topology_id) is not None:
        return
    now = datetime.now(timezone.utc)
    session.add(
        Topology(
            topology_id=topology_id,
            name=f"strict-topology-{topology_id}",
            version="v1",
            spec_json={},
            created_at=now,
            updated_at=now,
        ),
    )
    session.commit()


def _add_node(session: Session, *, default_topology_id: int | None = 99) -> None:
    session.add(
        ExecutionNode(
            node_key="n1",
            name="n1",
            provider_type=ExecutionNodeProviderType.LOCAL.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=4.0,
            total_memory_mb=8192,
            allocatable_cpu=4.0,
            allocatable_memory_mb=8192,
            default_topology_id=default_topology_id,
        ),
    )
    session.commit()


def _ws_job(session: Session, owner_id: int, job_type: str) -> tuple[Workspace, WorkspaceJob]:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="ws_strict",
        description="",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
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
    assert ws.workspace_id is not None
    return ws, job


def test_reconcile_without_runtime_raises_when_strict(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session)
        ws, job = _ws_job(session, uid, WorkspaceJobType.RECONCILE_RUNTIME.value)
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "production",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()

    with Session(bind_engine) as session:
        ws = session.get(Workspace, ws_id)
        job = session.get(WorkspaceJob, job_id)
        assert ws is not None and job is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            with pytest.raises(AuthoritativePlacementError, match="node_id and topology_id"):
                resolve_orchestrator_placement(session, ws, job)


def test_start_with_partial_runtime_raises_when_strict(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session)
        ws, job = _ws_job(session, uid, WorkspaceJobType.START.value)
        wid = ws.workspace_id
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                topology_id=None,
                container_id=None,
            ),
        )
        session.commit()
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "staging",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()

    with Session(bind_engine) as session:
        ws2 = session.get(Workspace, ws_id)
        job2 = session.get(WorkspaceJob, job_id)
        assert ws2 is not None and job2 is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            with pytest.raises(AuthoritativePlacementError, match="complete WorkspaceRuntime"):
                resolve_orchestrator_placement(session, ws2, job2)


def test_repo_import_reuses_complete_runtime_when_strict(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _seed_topology(session, 55)
        _add_node(session)
        ws, job = _ws_job(session, uid, WorkspaceJobType.REPO_IMPORT.value)
        wid = ws.workspace_id
        session.add(
            WorkspaceRuntime(
                workspace_id=wid,
                node_id="n1",
                topology_id=55,
                container_id="c1",
            ),
        )
        session.commit()
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "production",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()

    with Session(bind_engine) as session:
        ws = session.get(Workspace, ws_id)
        job = session.get(WorkspaceJob, job_id)
        assert ws is not None and job is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            nk, tid = resolve_orchestrator_placement(session, ws, job)
        assert nk == "n1"
        assert tid == 55


def test_repo_import_without_runtime_raises_when_strict(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session)
        ws, job = _ws_job(session, uid, WorkspaceJobType.REPO_IMPORT.value)
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "production",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()

    with Session(bind_engine) as session:
        ws = session.get(Workspace, ws_id)
        job = session.get(WorkspaceJob, job_id)
        assert ws is not None and job is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            with pytest.raises(AuthoritativePlacementError, match="node_id and topology_id"):
                resolve_orchestrator_placement(session, ws, job)


def test_create_requires_default_topology_on_node_when_strict(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _add_node(session, default_topology_id=None)
        ws, job = _ws_job(session, uid, WorkspaceJobType.CREATE.value)
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "production",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()

    with Session(bind_engine) as session:
        ws = session.get(Workspace, ws_id)
        job = session.get(WorkspaceJob, job_id)
        assert ws is not None and job is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            with pytest.raises(InvalidPlacementParametersError, match="default_topology_id"):
                resolve_orchestrator_placement(session, ws, job)


def test_snapshot_create_uses_workspace_execution_node_when_no_runtime(bind_engine: Engine) -> None:
    """Phase 3b Step 10: snapshot jobs must not fall back to control-plane DEVNEST_NODE_ID."""
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _seed_topology(session, 99)
        session.add(
            ExecutionNode(
                node_key="n-remote",
                name="n-remote",
                provider_type=ExecutionNodeProviderType.LOCAL.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
                default_topology_id=99,
            ),
        )
        session.commit()
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "n-remote")).first()
        assert node is not None and node.id is not None
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name="snap_ws",
            description="",
            owner_user_id=uid,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
            execution_node_id=int(node.id),
        )
        session.add(ws)
        session.flush()
        job = WorkspaceJob(
            workspace_id=ws.workspace_id,
            job_type=WorkspaceJobType.SNAPSHOT_CREATE.value,
            status=WorkspaceJobStatus.QUEUED.value,
            requested_by_user_id=uid,
            requested_config_version=1,
            attempt=0,
            workspace_snapshot_id=1,
        )
        session.add(job)
        session.commit()
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "production",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()

    with Session(bind_engine) as session:
        ws2 = session.get(Workspace, ws_id)
        job2 = session.get(WorkspaceJob, job_id)
        assert ws2 is not None and job2 is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            nk, tid = resolve_orchestrator_placement(session, ws2, job2)
        assert nk == "n-remote"
        assert tid == 99


def test_snapshot_create_raises_without_runtime_or_execution_node(bind_engine: Engine) -> None:
    with Session(bind_engine) as session:
        uid = _seed_user(session)
        _seed_topology(session, 99)
        _add_node(session, default_topology_id=99)
        ws, job = _ws_job(session, uid, WorkspaceJobType.SNAPSHOT_CREATE.value)
        ws_id = ws.workspace_id
        job_id = job.workspace_job_id

    fake_settings = type(
        "S",
        (),
        {
            "devnest_env": "development",
            "devnest_allow_runtime_env_fallback": True,
        },
    )()

    with Session(bind_engine) as session:
        ws2 = session.get(Workspace, ws_id)
        job2 = session.get(WorkspaceJob, job_id)
        assert ws2 is not None and job2 is not None
        with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake_settings):
            with pytest.raises(AuthoritativePlacementError, match="Snapshot job requires placement"):
                resolve_orchestrator_placement(session, ws2, job2)
