"""Integration tests: WorkspaceJob worker on PostgreSQL (worker DB, truncate per test).

Orchestrator calls are mocked with ``OrchestratorService`` autospec + result dataclasses; the
integration surface is real persistence (Workspace / WorkspaceRuntime / WorkspaceJob) and
transaction boundaries (caller ``commit``), matching how the worker is intended to be used.

Failure roll-ups for ``success=False`` results are covered in unit tests; this module focuses on
happy-path DB outcomes and a single orchestrator-exception path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.orchestrator_service.errors import WorkspaceBringUpError
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.workers.workspace_job_worker.worker import run_pending_jobs

NODE_ID = "node-int-1"
CONTAINER_ID = "ctr-int-abc"
CONTAINER_STATE = "running"
TOPOLOGY_ID_STR = "42"
INTERNAL_ENDPOINT = "http://10.0.0.5:8080"
REQUESTED_CONFIG_VERSION = 2


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="ws_job_worker_int_owner",
        email="ws_job_worker_int_owner@example.com",
        password_hash="not-used",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_workspace_with_configs(
    session: Session,
    owner_id: int,
    *,
    status: str,
    num_configs: int = 2,
    name: str = "Worker integration WS",
) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="integration worker",
        owner_user_id=owner_id,
        status=status,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    for v in range(1, num_configs + 1):
        session.add(
            WorkspaceConfig(
                workspace_id=ws.workspace_id,
                version=v,
                config_json={"version_marker": v},
            )
        )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def _seed_runtime(
    session: Session,
    workspace_id: int,
    *,
    node_id: str = "runtime-node",
    container_id: str = "runtime-ctr",
    config_version: int = 1,
) -> None:
    rt = WorkspaceRuntime(
        workspace_id=workspace_id,
        node_id=node_id,
        container_id=container_id,
        container_state="running",
        topology_id=1,
        internal_endpoint="http://old.internal",
        config_version=config_version,
        health_status=WorkspaceRuntimeHealthStatus.HEALTHY.value,
    )
    session.add(rt)
    session.commit()


def _seed_queued_job(
    session: Session,
    *,
    workspace_id: int,
    owner_id: int,
    job_type: str,
    requested_config_version: int = REQUESTED_CONFIG_VERSION,
    created_at: datetime | None = None,
) -> int:
    job = WorkspaceJob(
        workspace_id=workspace_id,
        job_type=job_type,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_id,
        requested_config_version=requested_config_version,
        attempt=0,
    )
    if created_at is not None:
        job.created_at = created_at
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.workspace_job_id is not None
    return job.workspace_job_id


def _orch() -> MagicMock:
    return create_autospec(OrchestratorService, instance=True)


def _bringup_ok(workspace_id: str) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=workspace_id,
        success=True,
        node_id=NODE_ID,
        topology_id=TOPOLOGY_ID_STR,
        container_id=CONTAINER_ID,
        container_state=CONTAINER_STATE,
        workspace_ip="10.0.0.5",
        internal_endpoint=INTERNAL_ENDPOINT,
        probe_healthy=True,
        issues=None,
    )


def _stop_ok(workspace_id: str) -> WorkspaceStopResult:
    return WorkspaceStopResult(
        workspace_id=workspace_id,
        success=True,
        container_id=CONTAINER_ID,
        container_state="stopped",
        topology_detached=True,
        issues=None,
    )


def _delete_ok(workspace_id: str) -> WorkspaceDeleteResult:
    return WorkspaceDeleteResult(
        workspace_id=workspace_id,
        success=True,
        container_deleted=True,
        topology_detached=True,
        issues=None,
    )


def _update_ok(workspace_id: str, *, config_version: int) -> WorkspaceUpdateResult:
    return WorkspaceUpdateResult(
        workspace_id=workspace_id,
        success=True,
        current_config_version=config_version,
        requested_config_version=config_version,
        update_strategy="noop",
        no_op=True,
        node_id=NODE_ID,
        topology_id=TOPOLOGY_ID_STR,
        container_id=CONTAINER_ID,
        container_state=CONTAINER_STATE,
        workspace_ip="10.0.0.5",
        internal_endpoint=INTERNAL_ENDPOINT,
        probe_healthy=True,
        issues=None,
    )


def test_process_create_job_happy_path_persists_runtime(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.CREATING.value,
        num_configs=2,
    )
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.CREATE.value,
        requested_config_version=REQUESTED_CONFIG_VERSION,
    )

    orch = _orch()
    orch.bring_up_workspace_runtime.return_value = _bringup_ok(str(wid))

    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    orch.bring_up_workspace_runtime.assert_called_once_with(
        workspace_id=str(wid),
        requested_config_version=REQUESTED_CONFIG_VERSION,
    )

    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert job is not None
    assert ws is not None
    assert rt is not None
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.started_at <= job.finished_at
    assert job.error_msg is None
    assert job.attempt == 1
    assert ws.status == WorkspaceStatus.RUNNING.value
    assert ws.endpoint_ref == INTERNAL_ENDPOINT
    assert ws.last_error_code is None
    assert ws.last_error_message is None
    assert rt.node_id == NODE_ID
    assert rt.container_id == CONTAINER_ID
    assert rt.container_state == CONTAINER_STATE
    assert rt.topology_id == int(TOPOLOGY_ID_STR)
    assert rt.internal_endpoint == INTERNAL_ENDPOINT
    assert rt.config_version == REQUESTED_CONFIG_VERSION
    assert rt.health_status == WorkspaceRuntimeHealthStatus.HEALTHY.value
    assert rt.last_heartbeat_at is not None


def test_process_start_job_happy_path(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.STOPPED.value,
        num_configs=2,
    )
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.START.value,
    )

    orch = _orch()
    orch.bring_up_workspace_runtime.return_value = _bringup_ok(str(wid))
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    orch.bring_up_workspace_runtime.assert_called_once_with(
        workspace_id=str(wid),
        requested_config_version=REQUESTED_CONFIG_VERSION,
    )
    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value


def test_process_stop_job_happy_path(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.RUNNING.value,
        num_configs=1,
    )
    _seed_runtime(db_session, wid)
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.STOP.value,
        requested_config_version=1,
    )

    orch = _orch()
    orch.stop_workspace_runtime.return_value = _stop_ok(str(wid))
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    orch.stop_workspace_runtime.assert_called_once_with(
        workspace_id=str(wid),
        requested_by=str(owner_id),
    )
    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert ws is not None and ws.status == WorkspaceStatus.STOPPED.value
    assert ws.last_stopped is not None
    assert rt is not None
    assert rt.container_state == "stopped"
    assert rt.health_status == WorkspaceRuntimeHealthStatus.UNKNOWN.value


def test_process_delete_job_happy_path_clears_runtime(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.DELETING.value,
        num_configs=1,
    )
    _seed_runtime(db_session, wid)
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.DELETE.value,
        requested_config_version=1,
    )

    orch = _orch()
    orch.delete_workspace_runtime.return_value = _delete_ok(str(wid))
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    orch.delete_workspace_runtime.assert_called_once_with(
        workspace_id=str(wid),
        requested_by=str(owner_id),
    )
    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert ws is not None and ws.status == WorkspaceStatus.DELETED.value
    assert ws.last_error_code is None
    assert rt is not None
    assert rt.container_id is None
    assert rt.container_state == "deleted"
    assert rt.internal_endpoint is None


def test_process_update_job_happy_path_respects_config_version(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.UPDATING.value,
        num_configs=2,
    )
    _seed_runtime(db_session, wid, config_version=1)
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.UPDATE.value,
        requested_config_version=REQUESTED_CONFIG_VERSION,
    )

    orch = _orch()
    orch.update_workspace_runtime.return_value = _update_ok(str(wid), config_version=REQUESTED_CONFIG_VERSION)
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    orch.update_workspace_runtime.assert_called_once_with(
        workspace_id=str(wid),
        requested_config_version=REQUESTED_CONFIG_VERSION,
        requested_by=str(owner_id),
    )
    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
    assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value
    assert rt is not None and rt.config_version == REQUESTED_CONFIG_VERSION
    assert rt.internal_endpoint == INTERNAL_ENDPOINT


def test_orchestrator_exception_marks_job_and_workspace_failed(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.CREATING.value,
        num_configs=2,
    )
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.CREATE.value,
    )

    orch = _orch()
    orch.bring_up_workspace_runtime.side_effect = WorkspaceBringUpError("integration injected failure")
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    assert job is not None
    assert job.status == WorkspaceJobStatus.FAILED.value
    assert job.finished_at is not None
    assert job.error_msg is not None
    assert "integration injected failure" in job.error_msg
    assert ws is not None
    assert ws.status == WorkspaceStatus.ERROR.value
    assert ws.last_error_code == "ORCHESTRATOR_EXCEPTION"


def test_unsupported_job_type_marks_failed_without_orchestrator(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.RUNNING.value,
        num_configs=1,
    )
    job_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type="NOT_A_REAL_TYPE",
        requested_config_version=1,
    )

    orch = _orch()
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    orch.bring_up_workspace_runtime.assert_not_called()
    job = db_session.get(WorkspaceJob, job_id)
    ws = db_session.get(Workspace, wid)
    assert job is not None and job.status == WorkspaceJobStatus.FAILED.value
    assert job.error_msg is not None
    assert ws is not None and ws.status == WorkspaceStatus.ERROR.value
    assert ws.last_error_code == "WORKSPACE_JOB_FAILED"


def test_run_pending_jobs_limit_one_leaves_second_queued_job(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner_id = _seed_owner(db_session)
    wid = _seed_workspace_with_configs(
        db_session,
        owner_id,
        status=WorkspaceStatus.RUNNING.value,
        num_configs=1,
    )
    _seed_runtime(db_session, wid)
    t_old = datetime(2024, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    t_new = datetime(2024, 3, 2, 10, 0, 0, tzinfo=timezone.utc)
    job_first_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.STOP.value,
        requested_config_version=1,
        created_at=t_old,
    )
    job_second_id = _seed_queued_job(
        db_session,
        workspace_id=wid,
        owner_id=owner_id,
        job_type=WorkspaceJobType.STOP.value,
        requested_config_version=1,
        created_at=t_new,
    )

    orch = _orch()
    orch.stop_workspace_runtime.return_value = _stop_ok(str(wid))
    run_pending_jobs(db_session, orch, limit=1)
    db_session.commit()

    assert orch.stop_workspace_runtime.call_count == 1
    first = db_session.get(WorkspaceJob, job_first_id)
    second = db_session.get(WorkspaceJob, job_second_id)
    assert first is not None and first.status == WorkspaceJobStatus.SUCCEEDED.value
    assert second is not None and second.status == WorkspaceJobStatus.QUEUED.value
