"""Integration: placement resolution before orchestrator build (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.orchestrator_service.app_factory import build_orchestrator_for_workspace_job
from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.placement_service.errors import NoSchedulableNodeError
from app.services.placement_service.orchestrator_binding import resolve_orchestrator_placement
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceStatus,
)
from app.workers.workspace_job_worker.worker import run_pending_jobs

pytestmark = pytest.mark.integration


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="placement_int_owner",
        email="placement_int_owner@example.com",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_create_job(
    session: Session,
    owner_id: int,
    *,
    max_attempts: int | None = None,
) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    node = ensure_default_local_execution_node(session)
    assert node.id is not None
    ws = Workspace(
        name="placement-ws",
        description="",
        owner_user_id=owner_id,
        status=WorkspaceStatus.CREATING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
        execution_node_id=int(node.id),
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}),
    )
    session.add(
        WorkspaceConfig(workspace_id=ws.workspace_id, version=2, config_json={}),
    )
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=WorkspaceJobType.CREATE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_id,
        requested_config_version=2,
        attempt=0,
    )
    if max_attempts is not None:
        job.max_attempts = max_attempts
    session.add(job)
    session.commit()
    session.refresh(job)
    assert ws.workspace_id is not None and job.workspace_job_id is not None
    return ws.workspace_id, job.workspace_job_id


def test_create_job_fails_when_placement_raises(db_session: Session) -> None:
    """Reserve failure happens before Docker; job should end FAILED with placement code."""
    owner = _seed_owner(db_session)
    _wid, jid = _seed_create_job(db_session, owner, max_attempts=1)

    with patch(
        "app.services.scheduler_service.service.reserve_node_for_workspace",
        side_effect=NoSchedulableNodeError("integration: no node"),
    ):
        run_pending_jobs(
            db_session,
            get_orchestrator=build_orchestrator_for_workspace_job,
            limit=1,
        )

    db_session.expire_all()
    job2 = db_session.get(WorkspaceJob, jid)
    assert job2 is not None
    assert job2.status == WorkspaceJobStatus.FAILED.value
    assert job2.error_msg and "no node" in job2.error_msg


def test_resolve_placement_create_uses_seeded_execution_node(db_session: Session) -> None:
    """Postgres fixture seeds a local ``ExecutionNode``; CREATE jobs resolve to its ``node_key``."""
    owner = _seed_owner(db_session)
    wid, jid = _seed_create_job(db_session, owner)
    ws = db_session.get(Workspace, wid)
    job = db_session.get(WorkspaceJob, jid)
    assert ws is not None and job is not None
    node_key, topology_id = resolve_orchestrator_placement(db_session, ws, job)
    assert node_key == default_local_node_key()
    assert isinstance(topology_id, int)


def test_build_orchestrator_wires_node_execution_for_placed_node(db_session: Session) -> None:
    """Orchestrator uses :mod:`node_execution_service` bundle (local Docker) for the placed ``node_key``."""
    owner = _seed_owner(db_session)
    wid, jid = _seed_create_job(db_session, owner)
    ws = db_session.get(Workspace, wid)
    job = db_session.get(WorkspaceJob, jid)
    assert ws is not None and job is not None
    mock_client = MagicMock()
    with patch(
        "app.services.node_execution_service.factory.docker.from_env",
        return_value=mock_client,
    ):
        orch = build_orchestrator_for_workspace_job(db_session, ws, job)
    assert orch._node_id == default_local_node_key()
    assert orch._runtime_adapter._client is mock_client
    mock_client.ping.assert_called()
