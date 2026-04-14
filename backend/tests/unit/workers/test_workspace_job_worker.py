"""Unit tests: workspace job worker dispatch and persistence (SQLite + mocked orchestrator)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlmodel import Session, select

from app.services.orchestrator_service.errors import AppOrchestratorBindingError, WorkspaceBringUpError
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceRestartResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)
from app.services.workspace_service.models import Workspace, WorkspaceEvent, WorkspaceJob, WorkspaceRuntime
from app.services.workspace_service.services.workspace_event_service import WorkspaceStreamEventType
from app.services.workspace_service.models.enums import (
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.workers.workspace_job_worker.worker import (
    load_next_queued_workspace_job,
    poll_workspace_jobs_tick,
    run_pending_jobs,
    run_queued_workspace_job_by_id,
)

# Orchestrator receives stringified workspace PK.
NODE_ID = "node-prod-1"
CONTAINER_ID = "ctr-abc123"
CONTAINER_STATE = "running"
TOPOLOGY_ID_STR = "42"
INTERNAL_ENDPOINT = "http://10.0.0.5:8080"
REQUESTED_CONFIG_VERSION = 2


@pytest.fixture
def patch_worker_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monotonic fake clock for ``worker._now``."""
    base = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def _tick() -> datetime:
        i = state["n"]
        state["n"] = i + 1
        return base + timedelta(seconds=i)

    import app.workers.workspace_job_worker.worker as worker_mod

    monkeypatch.setattr(worker_mod, "_now", _tick)


def _seed_workspace(
    session: Session,
    owner_user_id: int,
    *,
    status: str = WorkspaceStatus.STARTING.value,
    name: str = "Job Worker WS",
) -> Workspace:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=name,
        description="unit",
        owner_user_id=owner_user_id,
        status=status,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()
    assert ws.workspace_id is not None
    return ws


def _seed_job(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    job_type: str,
    status: str = WorkspaceJobStatus.QUEUED.value,
    requested_config_version: int = REQUESTED_CONFIG_VERSION,
    created_at: datetime | None = None,
    max_attempts: int = 1,
) -> WorkspaceJob:
    job = WorkspaceJob(
        workspace_id=workspace_id,
        job_type=job_type,
        status=status,
        requested_by_user_id=owner_user_id,
        requested_config_version=requested_config_version,
        attempt=0,
        max_attempts=max_attempts,
    )
    if created_at is not None:
        job.created_at = created_at
    session.add(job)
    session.flush()
    assert job.workspace_job_id is not None
    return job


def _seed_runtime(
    session: Session,
    workspace_id: int,
    *,
    node_id: str = "old-node",
    container_id: str = "old-ctr",
) -> WorkspaceRuntime:
    rt = WorkspaceRuntime(
        workspace_id=workspace_id,
        node_id=node_id,
        container_id=container_id,
        container_state="running",
        topology_id=1,
        internal_endpoint="http://old",
        config_version=1,
        health_status=WorkspaceRuntimeHealthStatus.UNKNOWN.value,
    )
    session.add(rt)
    session.flush()
    return rt


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


def _bringup_fail(workspace_id: str) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=workspace_id,
        success=False,
        node_id=NODE_ID,
        issues=["runtime:probe:unhealthy"],
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


def _stop_fail(workspace_id: str) -> WorkspaceStopResult:
    return WorkspaceStopResult(
        workspace_id=workspace_id,
        success=False,
        issues=["stop:engine:failed"],
    )


def _delete_ok(workspace_id: str) -> WorkspaceDeleteResult:
    return WorkspaceDeleteResult(
        workspace_id=workspace_id,
        success=True,
        container_deleted=True,
        topology_detached=True,
        issues=None,
    )


def _delete_fail(workspace_id: str) -> WorkspaceDeleteResult:
    return WorkspaceDeleteResult(
        workspace_id=workspace_id,
        success=False,
        issues=["delete:engine:failed"],
    )


def _restart_ok(workspace_id: str) -> WorkspaceRestartResult:
    return WorkspaceRestartResult(
        workspace_id=workspace_id,
        success=True,
        stop_success=True,
        bringup_success=True,
        node_id=NODE_ID,
        topology_id=TOPOLOGY_ID_STR,
        container_id=CONTAINER_ID,
        container_state=CONTAINER_STATE,
        workspace_ip="10.0.0.5",
        internal_endpoint=INTERNAL_ENDPOINT,
        probe_healthy=True,
        issues=None,
    )


def _update_ok(workspace_id: str, *, config_version: int = REQUESTED_CONFIG_VERSION) -> WorkspaceUpdateResult:
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


class TestLoadPendingJobs:
    def test_load_next_queued_picks_oldest_created_at(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
    ) -> None:
        t0 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.STOP.value,
                created_at=t1,
            )
            older = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.START.value,
                created_at=t0,
            )
            older_job_id = older.workspace_job_id
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            nxt = load_next_queued_workspace_job(session)
            assert nxt is not None
            assert nxt.workspace_job_id == older_job_id
            assert nxt.job_type == WorkspaceJobType.START.value

    def test_run_pending_jobs_respects_limit_two(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        wid_str_holder: dict[str, str] = {}

        def _bring(**kwargs: object) -> WorkspaceBringUpResult:
            return _bringup_ok(str(kwargs["workspace_id"]))

        orch.bring_up_workspace_runtime.side_effect = _bring

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            wid_str_holder["v"] = str(wid)
            _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
            _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.START.value,
                created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            tick = run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=2)
            session.commit()

        assert tick.processed_count == 2
        assert orch.bring_up_workspace_runtime.call_count == 2
        wid_str = wid_str_holder["v"]
        orch.bring_up_workspace_runtime.assert_any_call(
            workspace_id=wid_str,
            requested_config_version=REQUESTED_CONFIG_VERSION,
        )


class TestMarkJobStarted:
    def test_orchestrator_called_only_after_job_running(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        job_id_holder: dict[str, int] = {}

        def _bring(*, workspace_id: str, requested_config_version: int | None = None) -> WorkspaceBringUpResult:
            jid = job_id_holder["id"]
            with Session(workspace_job_worker_engine) as s:
                job = s.get(WorkspaceJob, jid)
                assert job is not None
                assert job.status == WorkspaceJobStatus.RUNNING.value
                assert job.started_at is not None
                assert job.finished_at is None
            return _bringup_ok(workspace_id)

        orch.bring_up_workspace_runtime.side_effect = _bring

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
            )
            assert job.workspace_job_id is not None
            job_id_holder["id"] = job.workspace_job_id
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.bring_up_workspace_runtime.assert_called_once()


class TestDispatchCreate:
    def test_create_dispatches_bring_up_and_persists_success(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.bring_up_workspace_runtime.return_value = _bringup_ok(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.bring_up_workspace_runtime.assert_called_once_with(
            workspace_id=str(wid),
            requested_config_version=REQUESTED_CONFIG_VERSION,
        )

        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None
            assert ws is not None
            assert rt is not None
            assert job.status == WorkspaceJobStatus.SUCCEEDED.value
            assert job.error_msg is None
            assert job.started_at is not None
            assert job.finished_at is not None
            assert job.started_at <= job.finished_at
            assert job.attempt == 1
            assert ws.status == WorkspaceStatus.RUNNING.value
            assert ws.endpoint_ref == INTERNAL_ENDPOINT
            assert ws.last_error_code is None
            assert rt.node_id == NODE_ID
            assert rt.container_id == CONTAINER_ID
            assert rt.container_state == CONTAINER_STATE
            assert rt.topology_id == int(TOPOLOGY_ID_STR)
            assert rt.internal_endpoint == INTERNAL_ENDPOINT
            assert rt.config_version == REQUESTED_CONFIG_VERSION
            assert rt.health_status == WorkspaceRuntimeHealthStatus.HEALTHY.value
            assert rt.last_heartbeat_at is not None

            evs = list(
                session.exec(
                    select(WorkspaceEvent)
                    .where(WorkspaceEvent.workspace_id == wid)
                    .order_by(WorkspaceEvent.workspace_event_id),
                ).all(),
            )
            assert len(evs) == 2
            assert evs[0].event_type == WorkspaceStreamEventType.JOB_RUNNING
            assert evs[1].event_type == WorkspaceStreamEventType.JOB_SUCCEEDED


class TestDispatchStart:
    def test_start_dispatches_bring_up(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.START.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.bring_up_workspace_runtime.return_value = _bringup_ok(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.bring_up_workspace_runtime.assert_called_once_with(
            workspace_id=str(wid),
            requested_config_version=REQUESTED_CONFIG_VERSION,
        )
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            assert job is not None
            assert job.status == WorkspaceJobStatus.SUCCEEDED.value


class TestDispatchStop:
    def test_stop_dispatches_stop_and_persists(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            _seed_runtime(session, wid)
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.STOP.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.stop_workspace_runtime.return_value = _stop_ok(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.stop_workspace_runtime.assert_called_once_with(
            workspace_id=str(wid),
            container_id="old-ctr",
            requested_by=str(owner_user_id),
        )
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
            assert ws is not None and ws.status == WorkspaceStatus.STOPPED.value
            assert ws.last_stopped is not None
            assert rt is not None
            assert rt.container_state == "stopped"
            assert rt.health_status == WorkspaceRuntimeHealthStatus.UNKNOWN.value


class TestDispatchRestart:
    def test_restart_dispatches_restart(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.RESTART.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.restart_workspace_runtime.return_value = _restart_ok(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.restart_workspace_runtime.assert_called_once_with(
            workspace_id=str(wid),
            container_id=None,
            requested_by=str(owner_user_id),
            requested_config_version=REQUESTED_CONFIG_VERSION,
        )
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
            assert rt is not None
            assert rt.node_id == NODE_ID
            assert rt.health_status == WorkspaceRuntimeHealthStatus.HEALTHY.value


class TestDispatchDelete:
    def test_delete_dispatches_delete_and_clears_runtime(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            _seed_runtime(session, wid)
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.DELETE.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.delete_workspace_runtime.return_value = _delete_ok(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.delete_workspace_runtime.assert_called_once_with(
            workspace_id=str(wid),
            container_id="old-ctr",
            requested_by=str(owner_user_id),
        )
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
            assert ws is not None and ws.status == WorkspaceStatus.DELETED.value
            assert rt is not None
            assert rt.container_id is None
            assert rt.container_state == "deleted"
            assert rt.internal_endpoint is None


class TestDispatchUpdate:
    def test_update_dispatches_update_with_config_version(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        cfg = 3

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.UPDATE.value,
                requested_config_version=cfg,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.update_workspace_runtime.return_value = _update_ok(str(wid), config_version=cfg)
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.update_workspace_runtime.assert_called_once_with(
            workspace_id=str(wid),
            container_id=None,
            requested_config_version=cfg,
            requested_by=str(owner_user_id),
        )
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
            assert rt is not None and rt.config_version == cfg

    def test_update_noop_container_not_running_settles_stopped_without_error(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        """Orchestrator noop (config already applied) but container stopped → STOPPED + job SUCCEEDED."""
        orch = _orch()
        cfg = 3

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.UPDATE.value,
                requested_config_version=cfg,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.update_workspace_runtime.return_value = WorkspaceUpdateResult(
                workspace_id=str(wid),
                success=False,
                current_config_version=cfg,
                requested_config_version=cfg,
                update_strategy="noop",
                no_op=True,
                node_id=NODE_ID,
                topology_id=TOPOLOGY_ID_STR,
                container_id="cid-exited",
                container_state="exited",
                issues=["update:noop:container_not_running:exited"],
            )
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
            assert job.error_msg is None
            assert ws is not None and ws.status == WorkspaceStatus.STOPPED.value
            assert ws.last_error_code is None
            assert ws.status_reason is not None
            assert rt is not None
            assert rt.config_version == cfg
            assert rt.health_status == WorkspaceRuntimeHealthStatus.UNKNOWN.value


class TestReconcileRuntimeJob:
    def test_reconcile_runtime_running_calls_health_check(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.RUNNING.value)
            wid = ws.workspace_id
            assert wid is not None
            _seed_runtime(session, wid)
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        orch.check_workspace_runtime_health.return_value = _bringup_ok(str(wid))

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.check_workspace_runtime_health.assert_called_once_with(
            workspace_id=str(wid),
            container_id="old-ctr",
        )
        orch.bring_up_workspace_runtime.assert_not_called()
        with Session(workspace_job_worker_engine) as session:
            job2 = session.get(WorkspaceJob, job_id)
            ws2 = session.get(Workspace, wid)
            assert job2 is not None and job2.status == WorkspaceJobStatus.SUCCEEDED.value
            assert ws2 is not None and ws2.status == WorkspaceStatus.RUNNING.value


class TestUnsupportedJobType:
    def test_unknown_job_type_marks_job_failed_and_workspace_error(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type="NOT_A_REAL_TYPE",
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.bring_up_workspace_runtime.assert_not_called()
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            assert job is not None
            assert job.status == WorkspaceJobStatus.FAILED.value
            assert job.finished_at is not None
            assert job.error_msg is not None
            assert ws is not None
            assert ws.status == WorkspaceStatus.ERROR.value
            assert ws.last_error_code == "WORKSPACE_JOB_FAILED"


class TestOrchestratorException:
    def test_bring_up_exception_marks_job_failed_and_records_orchestrator_code(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        orch.bring_up_workspace_runtime.side_effect = WorkspaceBringUpError("engine blew up")

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            assert job is not None
            assert job.status == WorkspaceJobStatus.FAILED.value
            assert job.finished_at is not None
            assert job.error_msg is not None
            assert "engine blew up" in job.error_msg
            assert ws is not None
            assert ws.status == WorkspaceStatus.ERROR.value
            assert ws.last_error_code == "ORCHESTRATOR_EXCEPTION"


class TestUnsuccessfulOrchestratorResult:
    def test_bring_up_false_success_does_not_mutate_runtime_snapshot(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            _seed_runtime(session, wid, node_id="keep-me", container_id="keep-ctr")
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.START.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.bring_up_workspace_runtime.return_value = _bringup_fail(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None
            assert job.status == WorkspaceJobStatus.FAILED.value
            assert job.error_msg is not None
            assert "runtime:probe:unhealthy" in job.error_msg
            assert ws is not None
            assert ws.status == WorkspaceStatus.ERROR.value
            assert ws.last_error_code == "WORKSPACE_JOB_FAILED"
            assert rt is not None
            assert rt.node_id == "keep-me"
            assert rt.container_id == "keep-ctr"

    def test_stop_false_success_leaves_runtime_unchanged(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            _seed_runtime(session, wid, node_id="n1", container_id="c1")
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.STOP.value,
            )
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            orch.stop_workspace_runtime.return_value = _stop_fail(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert rt is not None
            assert rt.container_id == "c1"
            assert rt.container_state == "running"

    def test_delete_false_success_keeps_workspace_non_deleted(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.DELETING.value)
            wid = ws.workspace_id
            assert wid is not None
            _seed_runtime(session, wid)
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.DELETE.value,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            orch.delete_workspace_runtime.return_value = _delete_fail(str(wid))
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
            assert job is not None and job.status == WorkspaceJobStatus.FAILED.value
            assert ws is not None and ws.status == WorkspaceStatus.ERROR.value
            assert rt is not None and rt.container_state == "running"


class TestRunQueuedJobById:
    def test_run_by_id_processes_that_job_even_when_not_fifo_next(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        t_old = datetime(2024, 2, 1, tzinfo=timezone.utc)
        t_new = datetime(2024, 2, 2, tzinfo=timezone.utc)
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            older = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.STOP.value,
                created_at=t_old,
            )
            older_id = older.workspace_job_id
            newer = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.START.value,
                created_at=t_new,
            )
            newer_id = newer.workspace_job_id
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            orch.bring_up_workspace_runtime.return_value = _bringup_ok(str(wid))
            tick = run_queued_workspace_job_by_id(
                session,
                get_orchestrator=lambda _s, _ws, _j: orch,
                workspace_job_id=newer_id,
            )
            session.commit()

        assert tick.processed_count == 1
        assert tick.last_job_id == newer_id
        with Session(workspace_job_worker_engine) as session:
            assert session.get(WorkspaceJob, newer_id).status == WorkspaceJobStatus.SUCCEEDED.value
            assert session.get(WorkspaceJob, older_id).status == WorkspaceJobStatus.QUEUED.value


class TestCallOrder:
    def test_events_order_mark_running_before_finalize(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        events: list[str] = []

        def _bring(*, workspace_id: str, requested_config_version: int | None = None) -> WorkspaceBringUpResult:
            events.append("orchestrator")
            return _bringup_ok(workspace_id)

        orch.bring_up_workspace_runtime.side_effect = _bring

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
            )
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            job = load_next_queued_workspace_job(session)
            assert job is not None
            assert job.status == WorkspaceJobStatus.QUEUED.value
            events.append("loaded_queued")
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            events.append("after_tick")
            session.commit()

        assert events == ["loaded_queued", "orchestrator", "after_tick"]


class TestMissingWorkspace:
    def test_missing_workspace_row_marks_job_failed_without_orchestrator(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            job = WorkspaceJob(
                workspace_id=9_999_999,
                job_type=WorkspaceJobType.START.value,
                status=WorkspaceJobStatus.QUEUED.value,
                requested_by_user_id=owner_user_id,
                requested_config_version=1,
                attempt=0,
                max_attempts=1,
            )
            session.add(job)
            session.flush()
            job_id = job.workspace_job_id
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        orch.bring_up_workspace_runtime.assert_not_called()
        with Session(workspace_job_worker_engine) as session:
            job = session.get(WorkspaceJob, job_id)
            assert job is not None
            assert job.status == WorkspaceJobStatus.FAILED.value
            assert job.error_msg is not None


class TestPollWorkspaceJobsTick:
    """``poll_workspace_jobs_tick`` is the bind-only entrypoint used by the poll-loop process."""

    def test_poll_processes_one_job_without_caller_session(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
            )
            job_id = job.workspace_job_id
            session.commit()

        orch.bring_up_workspace_runtime.return_value = _bringup_ok(str(wid))
        tick = poll_workspace_jobs_tick(
            workspace_job_worker_engine,
            get_orchestrator=lambda _s, _ws, _j: orch,
            limit=1,
        )
        assert tick.processed_count == 1
        assert tick.last_job_id == job_id

        with Session(workspace_job_worker_engine) as session:
            j = session.get(WorkspaceJob, job_id)
            assert j is not None and j.status == WorkspaceJobStatus.SUCCEEDED.value

    def test_poll_empty_queue_returns_zero_processed(
        self,
        workspace_job_worker_engine,
        patch_worker_now: None,
    ) -> None:
        orch = _orch()
        tick = poll_workspace_jobs_tick(
            workspace_job_worker_engine,
            get_orchestrator=lambda _s, _ws, _j: orch,
            limit=1,
        )
        assert tick.processed_count == 0
        assert tick.last_job_id is None
        orch.bring_up_workspace_runtime.assert_not_called()


class TestOrchestratorBindingFailure:
    def test_run_pending_jobs_fails_job_when_get_orchestrator_raises_binding_error(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
    ) -> None:
        def _boom(_s, _ws, _j):
            raise AppOrchestratorBindingError("Docker engine not available for local execution")

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id, status=WorkspaceStatus.CREATING.value)
            wid = ws.workspace_id
            assert wid is not None
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
            )
            jid = job.workspace_job_id
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=_boom, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            job2 = session.get(WorkspaceJob, jid)
            ws2 = session.get(Workspace, wid)
            assert job2 is not None and ws2 is not None
            assert job2.status == WorkspaceJobStatus.FAILED.value
            assert job2.error_msg and "Docker engine" in job2.error_msg
            assert ws2.status == WorkspaceStatus.ERROR.value
            assert ws2.last_error_code == "ORCHESTRATOR_BINDING_FAILED"


class TestWorkspaceJobRetry:
    def test_bring_up_failure_with_max_attempts_2_requeues_then_terminal(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.libs.common.config import get_settings

        monkeypatch.setenv("WORKSPACE_JOB_RETRY_BACKOFF_SECONDS", "0")
        get_settings.cache_clear()
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            orch.bring_up_workspace_runtime.return_value = _bringup_fail(str(wid))
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.CREATE.value,
                max_attempts=2,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            j1 = session.get(WorkspaceJob, job_id)
            ws1 = session.get(Workspace, wid)
            assert j1 is not None and ws1 is not None
            assert j1.status == WorkspaceJobStatus.QUEUED.value
            assert j1.next_attempt_after is not None
            assert j1.failure_stage == "CONTAINER"
            assert j1.attempt == 1
            assert ws1.status == WorkspaceStatus.STARTING.value

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            j2 = session.get(WorkspaceJob, job_id)
            ws2 = session.get(Workspace, wid)
            assert j2 is not None and ws2 is not None
            assert j2.status == WorkspaceJobStatus.FAILED.value
            assert j2.attempt == 2
            assert j2.max_attempts == 2
            assert ws2.status == WorkspaceStatus.ERROR.value

        get_settings.cache_clear()

    def test_bring_up_failure_second_attempt_succeeds(
        self,
        workspace_job_worker_engine,
        owner_user_id: int,
        patch_worker_now: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.libs.common.config import get_settings

        monkeypatch.setenv("WORKSPACE_JOB_RETRY_BACKOFF_SECONDS", "0")
        get_settings.cache_clear()
        orch = _orch()

        with Session(workspace_job_worker_engine) as session:
            ws = _seed_workspace(session, owner_user_id)
            wid = ws.workspace_id
            assert wid is not None
            wid_str = str(wid)
            orch.bring_up_workspace_runtime.side_effect = [
                _bringup_fail(wid_str),
                _bringup_ok(wid_str),
            ]
            job = _seed_job(
                session,
                workspace_id=wid,
                owner_user_id=owner_user_id,
                job_type=WorkspaceJobType.START.value,
                max_attempts=2,
            )
            session.commit()
            job_id = job.workspace_job_id

        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()
        with Session(workspace_job_worker_engine) as session:
            run_pending_jobs(session, get_orchestrator=lambda _s, _ws, _j: orch, limit=1)
            session.commit()

        with Session(workspace_job_worker_engine) as session:
            j = session.get(WorkspaceJob, job_id)
            ws = session.get(Workspace, wid)
            assert j is not None and ws is not None
            assert j.status == WorkspaceJobStatus.SUCCEEDED.value
            assert ws.status == WorkspaceStatus.RUNNING.value

        get_settings.cache_clear()
