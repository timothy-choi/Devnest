"""Integration tests: RECONCILE_RUNTIME worker + enqueue (PostgreSQL)."""

from __future__ import annotations

from unittest.mock import MagicMock, create_autospec, patch

import pytest
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import WorkspaceBringUpResult, WorkspaceStopResult
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceStatus,
)
from app.services.workspace_service.models.enums import WorkspaceRuntimeHealthStatus
from app.services.workspace_service.services.workspace_event_service import WorkspaceStreamEventType
from app.services.workspace_service.services import workspace_intent_service
from app.workers.workspace_job_worker.worker import run_pending_jobs

NODE_ID = "node-rc-1"
CONTAINER_ID = "ctr-rc"
TOPOLOGY_ID_STR = "99"
INTERNAL_EP = "http://10.55.0.1:8080"
CFG_V = 2


def _seed_owner(session: Session) -> int:
    user = UserAuth(
        username="reconcile_int_owner",
        email="reconcile_int_owner@example.com",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_running_workspace(session: Session, owner_id: int) -> int:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="rc-ws",
        description="",
        owner_user_id=owner_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    session.add(
        WorkspaceConfig(
            workspace_id=ws.workspace_id,
            version=1,
            config_json={"v": 1},
        )
    )
    session.add(
        WorkspaceConfig(
            workspace_id=ws.workspace_id,
            version=CFG_V,
            config_json={"v": CFG_V},
        )
    )
    session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id="old",
            container_id="old",
            container_state="running",
            topology_id=1,
            internal_endpoint="http://stale",
            config_version=CFG_V,
            health_status=WorkspaceRuntimeHealthStatus.UNKNOWN.value,
        )
    )
    session.commit()
    session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


def _health_ok(wid: str) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=wid,
        success=True,
        node_id=NODE_ID,
        topology_id=TOPOLOGY_ID_STR,
        container_id=CONTAINER_ID,
        container_state="running",
        workspace_ip="10.55.0.1",
        internal_endpoint=INTERNAL_EP,
        probe_healthy=True,
        issues=None,
    )


def _health_bad(wid: str) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=wid,
        success=False,
        container_id=None,
        container_state="missing",
        probe_healthy=False,
        issues=["health:container:not_found"],
    )


def _orch() -> MagicMock:
    return create_autospec(OrchestratorService, instance=True)


def test_reconcile_running_syncs_runtime_and_succeeds(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner = _seed_owner(db_session)
    wid = _seed_running_workspace(db_session, owner)
    job = WorkspaceJob(
        workspace_id=wid,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        status="QUEUED",
        requested_by_user_id=owner,
        requested_config_version=CFG_V,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    jid = job.workspace_job_id

    orch = _orch()
    orch.check_workspace_runtime_health.return_value = _health_ok(str(wid))

    run_pending_jobs(db_session, get_orchestrator=lambda _s: orch, limit=1)
    db_session.expire_all()

    orch.check_workspace_runtime_health.assert_called_once_with(workspace_id=str(wid))
    orch.stop_workspace_runtime.assert_not_called()

    job2 = db_session.get(WorkspaceJob, jid)
    assert job2 is not None
    assert job2.status == WorkspaceJobStatus.SUCCEEDED.value

    rt = db_session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    assert rt is not None
    assert rt.container_id == CONTAINER_ID
    assert rt.internal_endpoint == INTERNAL_EP

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.RUNNING.value

    ev = db_session.exec(
        select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == wid).order_by(WorkspaceEvent.workspace_event_id)
    ).all()
    types = [e.event_type for e in ev]
    assert WorkspaceStreamEventType.RECONCILE_STARTED in types
    assert WorkspaceStreamEventType.RECONCILE_FIXED_RUNTIME in types


def test_reconcile_running_unhealthy_marks_workspace_error(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    owner = _seed_owner(db_session)
    wid = _seed_running_workspace(db_session, owner)
    job = WorkspaceJob(
        workspace_id=wid,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        status="QUEUED",
        requested_by_user_id=owner,
        requested_config_version=CFG_V,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    jid = job.workspace_job_id

    orch = _orch()
    orch.check_workspace_runtime_health.return_value = _health_bad(str(wid))

    run_pending_jobs(db_session, get_orchestrator=lambda _s: orch, limit=1)
    db_session.expire_all()

    job2 = db_session.get(WorkspaceJob, jid)
    assert job2 is not None
    assert job2.status == WorkspaceJobStatus.FAILED.value

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.ERROR.value

    ev = db_session.exec(select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == wid)).all()
    assert any(e.event_type == WorkspaceStreamEventType.RECONCILE_FAILED for e in ev)


def test_reconcile_stopped_stops_lingering_container(
    db_session: Session,
    patch_worker_now: None,
) -> None:
    from datetime import datetime, timezone

    owner = _seed_owner(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="rc-stopped",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.STOPPED.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    for v in (1, CFG_V):
        db_session.add(
            WorkspaceConfig(workspace_id=ws.workspace_id, version=v, config_json={"v": v}),
        )
    db_session.add(
        WorkspaceRuntime(
            workspace_id=ws.workspace_id,
            node_id=NODE_ID,
            container_id=CONTAINER_ID,
            container_state="running",
            topology_id=99,
            internal_endpoint=INTERNAL_EP,
            config_version=CFG_V,
            health_status=WorkspaceRuntimeHealthStatus.HEALTHY.value,
        )
    )
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        status="QUEUED",
        requested_by_user_id=owner,
        requested_config_version=CFG_V,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    wid = ws.workspace_id
    jid = job.workspace_job_id

    orch = _orch()
    orch.check_workspace_runtime_health.return_value = _health_ok(str(wid))
    orch.stop_workspace_runtime.return_value = WorkspaceStopResult(
        workspace_id=str(wid),
        success=True,
        container_id=CONTAINER_ID,
        container_state="stopped",
        topology_detached=True,
        issues=None,
    )

    run_pending_jobs(db_session, get_orchestrator=lambda _s: orch, limit=1)
    db_session.expire_all()

    orch.stop_workspace_runtime.assert_called_once()
    job2 = db_session.get(WorkspaceJob, jid)
    assert job2 is not None
    assert job2.status == WorkspaceJobStatus.SUCCEEDED.value
    ws2 = db_session.get(Workspace, wid)
    assert ws2 is not None
    assert ws2.status == WorkspaceStatus.STOPPED.value


def test_enqueue_reconcile_runtime_via_service(
    db_session: Session,
) -> None:
    owner = _seed_owner(db_session)
    wid = _seed_running_workspace(db_session, owner)

    out = workspace_intent_service.enqueue_reconcile_runtime_job(db_session, workspace_id=wid)

    assert out.accepted is True
    assert out.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value
    assert out.status == WorkspaceStatus.RUNNING.value

    job = db_session.exec(select(WorkspaceJob).where(WorkspaceJob.workspace_id == wid)).first()
    assert job is not None
    assert job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value
    assert job.status == WorkspaceJobStatus.QUEUED.value


@patch("app.services.reconcile_service.reconcile_runtime.DevnestGatewayClient")
def test_reconcile_deleted_deregisters_when_route_present(
    mock_client_cls: MagicMock,
    db_session: Session,
    patch_worker_now: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_GATEWAY_URL", "http://127.0.0.1:9999")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()

    owner = _seed_owner(db_session)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="rc-del",
        description="",
        owner_user_id=owner,
        status=WorkspaceStatus.DELETED.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    wid = ws.workspace_id

    job = WorkspaceJob(
        workspace_id=wid,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        status="QUEUED",
        requested_by_user_id=owner,
        requested_config_version=1,
        attempt=0,
    )
    db_session.add(job)
    db_session.commit()
    jid = job.workspace_job_id

    inst = mock_client_cls.from_settings.return_value
    inst.get_registered_routes.return_value = [
        {"workspace_id": str(wid), "public_host": f"{wid}.app.devnest.local", "target": "http://x"},
    ]

    orch = _orch()
    run_pending_jobs(db_session, get_orchestrator=lambda _s: orch, limit=1)
    db_session.expire_all()

    inst.deregister_route.assert_called_once_with(str(wid))
    job2 = db_session.get(WorkspaceJob, jid)
    assert job2 is not None
    assert job2.status == WorkspaceJobStatus.SUCCEEDED.value

    get_settings.cache_clear()
