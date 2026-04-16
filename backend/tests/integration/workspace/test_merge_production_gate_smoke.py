"""Merge-tier smoke: strict placement, cleanup drain visibility, EC2-like execution node rows.

Runs in the default integration selector (no ``slow`` marker) and is included in CI merge-tier
slice for production-gate confidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.libs.topology.models import Topology
from app.services.auth_service.models import UserAuth
from app.services.cleanup_service import CLEANUP_SCOPE_BRINGUP_ROLLBACK, drain_pending_cleanup_tasks, ensure_durable_cleanup_task
from app.services.placement_service.errors import AuthoritativePlacementError
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.placement_service.orchestrator_binding import resolve_orchestrator_placement
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceCleanupTask,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceStatus,
)


def _strict_prod_settings() -> object:
    return type(
        "S",
        (),
        {
            "devnest_env": "production",
            "devnest_allow_runtime_env_fallback": False,
        },
    )()


def _ensure_topology_row(session: Session, topology_id: int) -> None:
    if session.get(Topology, topology_id) is not None:
        return
    now = datetime.now(timezone.utc)
    session.add(
        Topology(
            topology_id=topology_id,
            name=f"merge-gate-topology-{topology_id}",
            version="v1",
            spec_json={},
            created_at=now,
            updated_at=now,
        ),
    )
    session.commit()


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="merge_gate_owner",
        email="merge_gate_owner@example.com",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def test_merge_smoke_repo_import_strict_requires_runtime(db_session: Session) -> None:
    owner = _seed_owner(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="merge-ri",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=WorkspaceJobType.REPO_IMPORT.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner,
        requested_config_version=1,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(ws)
    db_session.refresh(job)

    with patch("app.services.placement_service.runtime_policy.get_settings", return_value=_strict_prod_settings()):
        with pytest.raises(AuthoritativePlacementError, match="node_id and topology_id"):
            resolve_orchestrator_placement(db_session, ws, job)


def test_merge_smoke_repo_import_strict_ok_with_runtime(db_session: Session) -> None:
    owner = _seed_owner(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="merge-ri2",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    wid = ws.workspace_id
    db_session.add(
        WorkspaceRuntime(
            workspace_id=wid,
            node_id="node-1",
            topology_id=42,
            container_id="cid",
        ),
    )
    job = WorkspaceJob(
        workspace_id=wid,
        job_type=WorkspaceJobType.REPO_IMPORT.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner,
        requested_config_version=1,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(ws)
    db_session.refresh(job)

    _ensure_topology_row(db_session, 42)
    with patch("app.services.placement_service.runtime_policy.get_settings", return_value=_strict_prod_settings()):
        nk, tid = resolve_orchestrator_placement(db_session, ws, job)
    assert nk == "node-1"
    assert tid == 42


def test_merge_smoke_cleanup_drain_deferred_without_reconcile(db_session: Session) -> None:
    owner = _seed_owner(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="merge-cl",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.ERROR.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    wid = ws.workspace_id
    db_session.add(
        WorkspaceRuntime(
            workspace_id=wid,
            node_id="node-1",
            topology_id=None,
            container_id=None,
        ),
    )
    ensure_durable_cleanup_task(db_session, workspace_id=wid, scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK, detail=["bringup"])
    db_session.commit()

    assert drain_pending_cleanup_tasks(db_session, limit_workspaces=4) == 0
    db_session.commit()
    task = db_session.exec(select(WorkspaceCleanupTask).where(WorkspaceCleanupTask.workspace_id == wid)).first()
    assert task is not None
    assert "runtime_placement_incomplete" in (task.detail or "")


def test_merge_smoke_ec2_like_node_strict_stop_uses_runtime(db_session: Session) -> None:
    owner = _seed_owner(db_session)
    db_session.add(
        ExecutionNode(
            node_key="ec2-merge-1",
            name="ec2-merge-1",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-mockmerge",
            execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
            hostname="10.0.1.50",
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=8.0,
            total_memory_mb=16384,
            allocatable_cpu=8.0,
            allocatable_memory_mb=16384,
            default_topology_id=100,
        ),
    )
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="merge-ec2",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    wid = ws.workspace_id
    db_session.add(
        WorkspaceRuntime(
            workspace_id=wid,
            node_id="ec2-merge-1",
            topology_id=100,
            container_id="ctr-ec2",
        ),
    )
    job = WorkspaceJob(
        workspace_id=wid,
        job_type=WorkspaceJobType.STOP.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner,
        requested_config_version=1,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(ws)
    db_session.refresh(job)

    _ensure_topology_row(db_session, 100)
    with patch("app.services.placement_service.runtime_policy.get_settings", return_value=_strict_prod_settings()):
        nk, tid = resolve_orchestrator_placement(db_session, ws, job)
    assert nk == "ec2-merge-1"
    assert tid == 100
