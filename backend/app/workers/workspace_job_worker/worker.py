"""DB-backed workspace job executor: dequeue ``QUEUED`` jobs, call orchestrator, persist outcomes.

**Dequeue / multi-runner semantics (PostgreSQL, SQLite 3.37+):** eligible rows are claimed with
``SELECT … FOR UPDATE SKIP LOCKED`` on the oldest ``QUEUED`` job. On **SQLite**, the claim
transaction starts with ``BEGIN IMMEDIATE`` so concurrent workers serialize on the DB file
(SQLite deferred transactions + ``SKIP LOCKED`` do not match PostgreSQL-style row races). On
PostgreSQL, row locks alone are sufficient. Each job is
processed in its **own** database session with an independent **commit** so a failure or rollback
on one job does not undo completed siblings in the same API tick.

- **Single runner:** FIFO processing; no contention.
- **Multiple runners / instances:** Safe concurrent dequeue; the same job is never executed twice
  unless an operator resets a stuck ``RUNNING`` row (out of scope: reconcile / watchdog).

:func:`load_next_queued_workspace_job` is **unlocked** and intended for tests or diagnostics only —
do not use it to drive execution.

**Persistence:** The worker is the system of record for :class:`~app.services.workspace_service.models.Workspace`,
:class:`~app.services.workspace_service.models.WorkspaceJob`, and
:class:`~app.services.workspace_service.models.WorkspaceRuntime` after orchestration. The orchestrator
returns result DTOs only; this module maps them onto ORM rows and emits workspace stream events.

Best-effort **route-admin** registration (``DEVNEST_GATEWAY_ENABLED``) runs after RUNNING / stop /
delete finalization; failures are logged only. On ``NoSchedulableNodeError``, optional EC2 autoscaler
hook (``devnest_autoscaler_*`` settings) may start one provision before the job is marked failed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

from app.services.orchestrator_service.errors import (
    AppOrchestratorBindingError,
    WorkspaceBringUpError,
    WorkspaceDeleteError,
    WorkspaceRestartError,
    WorkspaceStopError,
    WorkspaceUpdateError,
)
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.autoscaler_service.service import maybe_provision_on_no_schedulable_capacity
from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.placement_service.errors import NoSchedulableNodeError, PlacementError
from app.services.gateway_client.gateway_client import DevnestGatewayClient
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceRestartResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceJob,
    WorkspaceRuntime,
)
from app.services.workspace_service.models.enums import (
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntimeHealthStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    record_workspace_event,
)

from app.libs.common.config import get_settings
from app.libs.observability.correlation import correlation_scope
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability import metrics as devnest_metrics

from .errors import UnsupportedWorkspaceJobTypeError
from .results import WorkspaceJobWorkerTickResult

_ORCHESTRATOR_EXCEPTIONS: tuple[type[Exception], ...] = (
    WorkspaceBringUpError,
    WorkspaceStopError,
    WorkspaceDeleteError,
    WorkspaceRestartError,
    WorkspaceUpdateError,
)

_ERROR_CODE_JOB = "WORKSPACE_JOB_FAILED"
_ERROR_CODE_ORCH = "ORCHESTRATOR_EXCEPTION"
_ERROR_CODE_PLACEMENT = "PLACEMENT_FAILED"
_ERROR_CODE_ORCHESTRATOR_BINDING = "ORCHESTRATOR_BINDING_FAILED"


def _clear_runtime_capacity_reservation(session: Session, workspace_id: int) -> None:
    """Zero ``WorkspaceRuntime.reserved_*`` when workspace moves to a non-scheduling terminal error path."""
    rt = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    if rt is None:
        return
    if float(rt.reserved_cpu or 0) <= 0 and int(rt.reserved_memory_mb or 0) <= 0:
        return
    rt.reserved_cpu = 0.0
    rt.reserved_memory_mb = 0
    rt.updated_at = _now()
    session.add(rt)


def _fail_job_from_placement(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    message: str,
    *,
    placement_reason: str = "placement",
) -> None:
    devnest_metrics.record_placement_failure(reason=placement_reason)
    _mark_job_failed(session, job, message)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_PLACEMENT, message)
    _touch_workspace(session, ws)
    assert ws.workspace_id is not None
    _clear_runtime_capacity_reservation(session, ws.workspace_id)


def _fail_job_from_orchestrator_binding(session: Session, ws: Workspace, job: WorkspaceJob, message: str) -> None:
    """Node execution / Docker / SSH binding failed before orchestrator could run the job."""
    devnest_metrics.record_placement_failure(reason="orchestrator_binding")
    _mark_job_failed(session, job, message)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_ORCHESTRATOR_BINDING, message)
    _touch_workspace(session, ws)
    assert ws.workspace_id is not None
    _clear_runtime_capacity_reservation(session, ws.workspace_id)


def _worker_sessionmaker(bind: Engine):
    """Session factory for per-job transactions (same engine as API / poller)."""
    return sessionmaker(
        bind=bind,
        class_=Session,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


def _stmt_oldest_queued_job_for_update():
    return (
        select(WorkspaceJob)
        .where(WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value)
        .order_by(WorkspaceJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _stmt_queued_job_by_id_for_update(workspace_job_id: int):
    return (
        select(WorkspaceJob)
        .where(
            WorkspaceJob.workspace_job_id == workspace_job_id,
            WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value,
        )
        .with_for_update(skip_locked=True)
    )


def _begin_sqlite_immediate_claim_transaction(session: Session) -> None:
    """
    Reserve a SQLite write lock before dequeuing.

    Without this, two connections can both observe ``QUEUED`` under default deferred transactions,
    so ``FOR UPDATE SKIP LOCKED`` does not reliably exclude the second claimer (unlike PostgreSQL).
    Must be the first statement on a fresh session used only for claim+job work.
    """
    if session.get_bind().dialect.name != "sqlite":
        return
    session.execute(sa_text("BEGIN IMMEDIATE"))


def try_claim_next_queued_workspace_job(session: Session) -> WorkspaceJob | None:
    """
    Lock the oldest ``QUEUED`` row (``SKIP LOCKED``), transition it to ``RUNNING``, and flush.

    Caller must commit or rollback the session. Returns ``None`` if no job is available or all
    candidates are locked by other transactions.
    """
    _begin_sqlite_immediate_claim_transaction(session)
    job = session.exec(_stmt_oldest_queued_job_for_update()).first()
    if job is None:
        return None
    _mark_job_running(session, job)
    session.flush()
    return job


def try_claim_queued_workspace_job_by_id(session: Session, workspace_job_id: int) -> WorkspaceJob | None:
    """Same as :func:`try_claim_next_queued_workspace_job` but for a specific primary key."""
    _begin_sqlite_immediate_claim_transaction(session)
    job = session.exec(_stmt_queued_job_by_id_for_update(workspace_job_id)).first()
    if job is None:
        return None
    _mark_job_running(session, job)
    session.flush()
    return job


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(s: str | None, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _format_issues(issues: list[str] | None) -> str | None:
    if not issues:
        return None
    return _truncate("; ".join(str(x) for x in issues if str(x).strip()), 8192)


def _update_noop_issues_imply_stopped_workspace(issues: list[str] | None) -> bool:
    """
    True when orchestrator noop-update failed only because the workspace container is missing
    or not running (no restart was required; config version already matched).

    In those cases the settled control-plane state should be ``STOPPED``, not ``ERROR``.
    """
    if not issues:
        return False
    for raw in issues:
        s = str(raw).strip()
        if s.startswith("update:noop:workspace_runtime_not_found"):
            return True
        if s.startswith("update:noop:container_not_running:"):
            return True
    return False


def _parse_topology_id(val: str | None) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return int(s, 10)
    except ValueError:
        return None


def _health_from_probe(probe: bool | None) -> str:
    if probe is True:
        return WorkspaceRuntimeHealthStatus.HEALTHY.value
    if probe is False:
        return WorkspaceRuntimeHealthStatus.UNHEALTHY.value
    return WorkspaceRuntimeHealthStatus.UNKNOWN.value


def _get_or_create_runtime(session: Session, workspace_id: int) -> WorkspaceRuntime:
    """Return existing ``WorkspaceRuntime`` for ``workspace_id`` or insert a stub row."""
    row = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    if row is not None:
        return row
    rt = WorkspaceRuntime(workspace_id=workspace_id)
    session.add(rt)
    session.flush()
    return rt


def _apply_runtime_bringup_like(
    session: Session,
    workspace_id: int,
    *,
    node_id: str | None,
    topology_id: str | None,
    container_id: str | None,
    container_state: str | None,
    internal_endpoint: str | None,
    config_version: int,
    probe_healthy: bool | None,
    reserved_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    reserved_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
) -> None:
    """Persist placement + health snapshot after a successful bring-up / restart / update (running)."""
    rt = _get_or_create_runtime(session, workspace_id)
    ts = _now()
    rt.node_id = node_id
    rt.topology_id = _parse_topology_id(topology_id)
    rt.container_id = container_id
    rt.container_state = container_state
    rt.internal_endpoint = internal_endpoint
    rt.config_version = config_version
    nk = (node_id or "").strip()
    if nk:
        rt.reserved_cpu = float(reserved_cpu)
        rt.reserved_memory_mb = int(reserved_memory_mb)
    else:
        rt.reserved_cpu = 0.0
        rt.reserved_memory_mb = 0
    rt.health_status = _health_from_probe(probe_healthy)
    if probe_healthy is True:
        rt.last_heartbeat_at = ts
    rt.updated_at = ts
    session.add(rt)


def _apply_runtime_stop(session: Session, workspace_id: int, result: WorkspaceStopResult) -> None:
    """Update runtime row after stop: container id/state if known; health unknown."""
    rt = _get_or_create_runtime(session, workspace_id)
    ts = _now()
    if result.container_id is not None:
        rt.container_id = result.container_id
    if result.container_state is not None:
        rt.container_state = result.container_state
    rt.reserved_cpu = 0.0
    rt.reserved_memory_mb = 0
    rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
    rt.updated_at = ts
    session.add(rt)


def _clear_runtime_after_delete(session: Session, workspace_id: int) -> None:
    """Tombstone runtime row when workspace is deleted (container cleared, state ``deleted``)."""
    row = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    if row is None:
        return
    ts = _now()
    row.container_id = None
    row.container_state = "deleted"
    row.internal_endpoint = None
    row.reserved_cpu = 0.0
    row.reserved_memory_mb = 0
    row.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
    row.last_heartbeat_at = None
    row.updated_at = ts
    session.add(row)


def _mark_job_running(session: Session, job: WorkspaceJob) -> None:
    job.status = WorkspaceJobStatus.RUNNING.value
    job.started_at = _now()
    job.attempt = int(job.attempt or 0) + 1
    session.add(job)


def _mark_job_succeeded(session: Session, job: WorkspaceJob) -> None:
    job.status = WorkspaceJobStatus.SUCCEEDED.value
    job.finished_at = _now()
    job.error_msg = None
    session.add(job)
    devnest_metrics.record_job_terminal(
        job_type=job.job_type or "unknown",
        status=WorkspaceJobStatus.SUCCEEDED.value,
    )
    if job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value:
        devnest_metrics.record_reconcile_terminal(succeeded=True)


def _mark_job_failed(session: Session, job: WorkspaceJob, message: str | None) -> None:
    job.status = WorkspaceJobStatus.FAILED.value
    job.finished_at = _now()
    job.error_msg = _truncate(message, 8192)
    session.add(job)
    devnest_metrics.record_job_terminal(
        job_type=job.job_type or "unknown",
        status=WorkspaceJobStatus.FAILED.value,
    )
    if job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value:
        devnest_metrics.record_reconcile_terminal(succeeded=False)


def _workspace_clear_errors(ws: Workspace) -> None:
    ws.last_error_code = None
    ws.last_error_message = None
    ws.status_reason = None


def _workspace_set_error(ws: Workspace, code: str, message: str) -> None:
    ws.last_error_code = _truncate(code, 64)
    ws.last_error_message = _truncate(message, 4096)
    ws.status_reason = _truncate(message, 1024)


def _touch_workspace(session: Session, ws: Workspace) -> None:
    ws.updated_at = _now()
    session.add(ws)


def _gateway_default_public_host(workspace_id: int, base_domain: str) -> str:
    dom = (base_domain or "app.devnest.local").strip().strip(".")
    return f"{workspace_id}.{dom}"


def _gateway_try_register_running(ws: Workspace, internal_endpoint: str | None) -> None:
    """Notify route-admin after RUNNING; failures are logged only (control plane stays authoritative)."""
    try:
        settings = get_settings()
        if not settings.devnest_gateway_enabled:
            return
        ep = (internal_endpoint or "").strip()
        if not ep:
            logger.debug(
                "gateway_register_skipped_no_internal_endpoint",
                extra={"workspace_id": ws.workspace_id},
            )
            return
        wid = ws.workspace_id
        if wid is None:
            return
        public = (ws.public_host or "").strip() or _gateway_default_public_host(
            int(wid),
            settings.devnest_base_domain,
        )
        DevnestGatewayClient.from_settings(settings).register_route(str(wid), ep, public)
    except Exception as e:
        logger.warning(
            "gateway_register_failed_best_effort",
            extra={"workspace_id": getattr(ws, "workspace_id", None), "error": str(e)},
        )


def _gateway_try_deregister(workspace_id: int) -> None:
    """Remove route on stop/delete; failures are logged only."""
    try:
        settings = get_settings()
        if not settings.devnest_gateway_enabled:
            return
        DevnestGatewayClient.from_settings(settings).deregister_route(str(workspace_id))
    except Exception as e:
        logger.warning(
            "gateway_deregister_failed_best_effort",
            extra={"workspace_id": workspace_id, "error": str(e)},
        )


def _finalize_job_failed_workspace_error(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    *,
    message: str,
) -> None:
    """Mark job failed, move workspace to ``ERROR`` with operational error code (orchestration outcome)."""
    _mark_job_failed(session, job, message)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, message)
    _touch_workspace(session, ws)
    if ws.workspace_id is not None:
        _clear_runtime_capacity_reservation(session, ws.workspace_id)


def _finalize_runtime_running_success(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    *,
    config_version: int,
    node_id: str | None,
    topology_id: str | None,
    container_id: str | None,
    container_state: str | None,
    internal_endpoint: str | None,
    probe_healthy: bool | None,
    reserved_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    reserved_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
) -> None:
    """
    Shared success path for CREATE/START, RESTART, and UPDATE (restart path): persist runtime,
    mark job succeeded, set workspace ``RUNNING`` and clear last error fields.
    """
    wid = ws.workspace_id
    assert wid is not None
    _apply_runtime_bringup_like(
        session,
        wid,
        node_id=node_id,
        topology_id=topology_id,
        container_id=container_id,
        container_state=container_state,
        internal_endpoint=internal_endpoint,
        config_version=config_version,
        probe_healthy=probe_healthy,
        reserved_cpu=reserved_cpu,
        reserved_memory_mb=reserved_memory_mb,
    )
    _mark_job_succeeded(session, job)
    ws.status = WorkspaceStatus.RUNNING.value
    _workspace_clear_errors(ws)
    ws.endpoint_ref = internal_endpoint or ws.endpoint_ref
    ws.last_started = _now()
    _touch_workspace(session, ws)
    _gateway_try_register_running(ws, internal_endpoint)


def _finalize_bringup_result(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    result: WorkspaceBringUpResult,
    *,
    config_version: int,
) -> None:
    wid = ws.workspace_id
    assert wid is not None
    if result.success:
        _finalize_runtime_running_success(
            session,
            ws,
            job,
            config_version=config_version,
            node_id=result.node_id,
            topology_id=result.topology_id,
            container_id=result.container_id,
            container_state=result.container_state,
            internal_endpoint=result.internal_endpoint,
            probe_healthy=result.probe_healthy,
        )
        return

    msg = _format_issues(result.issues) or "Bring-up completed without success"
    _finalize_job_failed_workspace_error(session, ws, job, message=msg)


def _finalize_stop_result(session: Session, ws: Workspace, job: WorkspaceJob, result: WorkspaceStopResult) -> None:
    wid = ws.workspace_id
    assert wid is not None
    if result.success:
        _apply_runtime_stop(session, wid, result)
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.STOPPED.value
        _workspace_clear_errors(ws)
        ws.last_stopped = _now()
        _touch_workspace(session, ws)
        _gateway_try_deregister(wid)
        return

    msg = _format_issues(result.issues) or "Stop completed without success"
    _finalize_job_failed_workspace_error(session, ws, job, message=msg)


def _finalize_delete_result(session: Session, ws: Workspace, job: WorkspaceJob, result: WorkspaceDeleteResult) -> None:
    wid = ws.workspace_id
    assert wid is not None
    if result.success:
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.DELETED.value
        _workspace_clear_errors(ws)
        _clear_runtime_after_delete(session, wid)
        _touch_workspace(session, ws)
        _gateway_try_deregister(wid)
        return

    msg = _format_issues(result.issues) or "Delete completed without success"
    _finalize_job_failed_workspace_error(session, ws, job, message=msg)


def _finalize_restart_result(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    result: WorkspaceRestartResult,
    *,
    config_version: int,
) -> None:
    wid = ws.workspace_id
    assert wid is not None
    if result.success:
        _finalize_runtime_running_success(
            session,
            ws,
            job,
            config_version=config_version,
            node_id=result.node_id,
            topology_id=result.topology_id,
            container_id=result.container_id,
            container_state=result.container_state,
            internal_endpoint=result.internal_endpoint,
            probe_healthy=result.probe_healthy,
        )
        return

    msg = _format_issues(result.issues) or "Restart completed without success"
    _finalize_job_failed_workspace_error(session, ws, job, message=msg)


def _finalize_update_result(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    result: WorkspaceUpdateResult,
) -> None:
    wid = ws.workspace_id
    assert wid is not None
    cfg_v = int(result.requested_config_version or job.requested_config_version)
    if result.success:
        _finalize_runtime_running_success(
            session,
            ws,
            job,
            config_version=cfg_v,
            node_id=result.node_id,
            topology_id=result.topology_id,
            container_id=result.container_id,
            container_state=result.container_state,
            internal_endpoint=result.internal_endpoint,
            probe_healthy=result.probe_healthy,
        )
        return

    msg = _format_issues(result.issues) or "Update completed without success"
    if result.no_op and _update_noop_issues_imply_stopped_workspace(result.issues):
        # Config already matched; container absent or stopped — settle to STOPPED (not ERROR).
        rt = _get_or_create_runtime(session, wid)
        ts = _now()
        rt.node_id = result.node_id
        rt.topology_id = _parse_topology_id(result.topology_id)
        rt.container_id = result.container_id
        rt.container_state = result.container_state
        rt.internal_endpoint = result.internal_endpoint
        rt.config_version = cfg_v
        rt.reserved_cpu = 0.0
        rt.reserved_memory_mb = 0
        rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
        rt.last_heartbeat_at = None
        rt.updated_at = ts
        session.add(rt)

        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.STOPPED.value
        _workspace_clear_errors(ws)
        ws.status_reason = _truncate(msg, 1024)
        _touch_workspace(session, ws)
        return

    _finalize_job_failed_workspace_error(session, ws, job, message=msg)


def _execute_job_body(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
    """Dispatch ``job.job_type`` to the orchestrator and map the result to workspace/job/runtime rows."""
    wid = ws.workspace_id
    assert wid is not None
    wid_str = str(wid)
    requested_by = str(job.requested_by_user_id)
    jt = job.job_type
    cfg_v = int(job.requested_config_version)

    if jt in (WorkspaceJobType.CREATE.value, WorkspaceJobType.START.value):
        result = orchestrator.bring_up_workspace_runtime(
            workspace_id=wid_str,
            requested_config_version=cfg_v,
        )
        _finalize_bringup_result(session, ws, job, result, config_version=cfg_v)
        return

    if jt == WorkspaceJobType.STOP.value:
        result = orchestrator.stop_workspace_runtime(workspace_id=wid_str, requested_by=requested_by)
        _finalize_stop_result(session, ws, job, result)
        return

    if jt == WorkspaceJobType.DELETE.value:
        result = orchestrator.delete_workspace_runtime(workspace_id=wid_str, requested_by=requested_by)
        _finalize_delete_result(session, ws, job, result)
        return

    if jt == WorkspaceJobType.RESTART.value:
        result = orchestrator.restart_workspace_runtime(
            workspace_id=wid_str,
            requested_by=requested_by,
            requested_config_version=cfg_v,
        )
        _finalize_restart_result(session, ws, job, result, config_version=cfg_v)
        return

    if jt == WorkspaceJobType.UPDATE.value:
        result = orchestrator.update_workspace_runtime(
            workspace_id=wid_str,
            requested_config_version=cfg_v,
            requested_by=requested_by,
        )
        _finalize_update_result(session, ws, job, result)
        return

    if jt == WorkspaceJobType.RECONCILE_RUNTIME.value:
        from app.services.reconcile_service.reconcile_runtime import execute_reconcile_runtime_job

        execute_reconcile_runtime_job(session, orchestrator, ws, job)
        return

    raise UnsupportedWorkspaceJobTypeError(f"Unsupported WorkspaceJob.type={jt!r}")


def _emit_job_outcome_event(session: Session, *, wid: int, ws: Workspace, job: WorkspaceJob) -> None:
    """Append ``JOB_SUCCEEDED`` / ``JOB_FAILED`` stream event after final job status is known."""
    # Persist job/workspace mutations before refresh so we do not reload stale pre-success rows from DB.
    session.flush()
    session.refresh(job)
    session.refresh(ws)
    jid = job.workspace_job_id
    base_payload: dict[str, object] = {
        "job_id": jid,
        "job_type": job.job_type,
        "workspace_status": ws.status,
    }
    if job.status == WorkspaceJobStatus.SUCCEEDED.value:
        log_event(
            logger,
            LogEvent.WORKSPACE_JOB_SUCCEEDED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=jid,
            job_type=job.job_type,
        )
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
            status=ws.status,
            message="Workspace job succeeded",
            payload=base_payload,
        )
    elif job.status == WorkspaceJobStatus.FAILED.value:
        log_event(
            logger,
            LogEvent.WORKSPACE_JOB_FAILED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=jid,
            job_type=job.job_type,
            error_msg=(job.error_msg or "")[:500] if job.error_msg else None,
        )
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_FAILED,
            status=ws.status,
            message="Workspace job failed",
            payload={
                **base_payload,
                "error_msg": job.error_msg,
                "last_error_code": ws.last_error_code,
                "last_error_message": ws.last_error_message,
            },
        )


def _process_claimed_running_job(session: Session, orchestrator: OrchestratorService, job: WorkspaceJob) -> None:
    """
    Execute a job row that is already ``RUNNING`` (claimed via ``FOR UPDATE SKIP LOCKED``).

    Persists outcomes and emits ``JOB_RUNNING`` / outcome events. Caller owns commit/rollback.
    """
    wid = job.workspace_id
    jid = job.workspace_job_id
    jt = job.job_type
    ws = session.get(Workspace, wid)
    if ws is None:
        logger.error(
            "workspace_job_missing_workspace",
            extra={"workspace_id": wid, "workspace_job_id": jid, "job_type": jt},
        )
        _mark_job_failed(session, job, "Workspace row not found for job")
        return

    log_event(
        logger,
        LogEvent.WORKSPACE_JOB_STARTED,
        correlation_id=job.correlation_id,
        workspace_id=wid,
        workspace_job_id=jid,
        job_type=jt,
        attempt=int(job.attempt or 0),
    )
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.JOB_RUNNING,
        status=ws.status,
        message="Workspace job started",
        payload={
            "job_id": job.workspace_job_id,
            "job_type": job.job_type,
            "attempt": job.attempt,
        },
    )

    try:
        _execute_job_body(session, orchestrator, ws, job)
    except _ORCHESTRATOR_EXCEPTIONS as e:
        logger.warning(
            "workspace_job_orchestrator_exception",
            extra={
                "workspace_id": wid,
                "workspace_job_id": jid,
                "job_type": jt,
                "error": str(e),
            },
        )
        _mark_job_failed(session, job, str(e))
        ws.status = WorkspaceStatus.ERROR.value
        _workspace_set_error(ws, _ERROR_CODE_ORCH, str(e))
        _touch_workspace(session, ws)
        _clear_runtime_capacity_reservation(session, wid)
    except UnsupportedWorkspaceJobTypeError as e:
        logger.error(
            "workspace_job_unsupported_type",
            extra={"workspace_id": wid, "workspace_job_id": jid, "job_type": jt, "error": str(e)},
        )
        _mark_job_failed(session, job, str(e))
        ws.status = WorkspaceStatus.ERROR.value
        _workspace_set_error(ws, _ERROR_CODE_JOB, str(e))
        _touch_workspace(session, ws)
        _clear_runtime_capacity_reservation(session, wid)

    _emit_job_outcome_event(session, wid=wid, ws=ws, job=job)


def _process_next_queued_job_return_id(
    session: Session,
    get_orchestrator: Callable[[Session, Workspace, WorkspaceJob], OrchestratorService],
) -> int | None:
    """Claim one job under the current transaction and run it; return its id, or ``None`` if queue empty."""
    job = try_claim_next_queued_workspace_job(session)
    if job is None:
        return None
    jid = job.workspace_job_id
    assert jid is not None
    wid = job.workspace_id
    with correlation_scope(job.correlation_id):
        ws = session.get(Workspace, wid)
        if ws is None:
            _mark_job_failed(session, job, "Workspace row not found for job")
            return jid
        try:
            orchestrator = get_orchestrator(session, ws, job)
        except PlacementError as e:
            if isinstance(e, NoSchedulableNodeError):
                log_event(
                    logger,
                    LogEvent.PLACEMENT_NO_SCHEDULABLE_NODE,
                    correlation_id=job.correlation_id,
                    workspace_id=wid,
                    workspace_job_id=jid,
                    job_type=job.job_type,
                    detail=str(e)[:500],
                )
                try:
                    maybe_provision_on_no_schedulable_capacity(session)
                except Exception:
                    logger.exception("autoscaler_provision_on_no_capacity_unexpected_error")
            logger.warning(
                "workspace_job_placement_failed",
                extra={
                    "workspace_id": wid,
                    "workspace_job_id": jid,
                    "job_type": job.job_type,
                    "error": str(e),
                },
            )
            pr = "no_schedulable_node" if isinstance(e, NoSchedulableNodeError) else "placement"
            _fail_job_from_placement(session, ws, job, str(e), placement_reason=pr)
            return jid
        except AppOrchestratorBindingError as e:
            logger.warning(
                "workspace_job_orchestrator_binding_failed",
                extra={
                    "workspace_id": wid,
                    "workspace_job_id": jid,
                    "job_type": job.job_type,
                    "error": str(e),
                },
            )
            _fail_job_from_orchestrator_binding(session, ws, job, str(e))
            return jid
        _process_claimed_running_job(session, orchestrator, job)
    return jid


def load_next_queued_workspace_job(session: Session) -> WorkspaceJob | None:
    """
    Return the oldest ``QUEUED`` workspace job without row locking.

    **Not safe for execution** under concurrent runners — use :func:`try_claim_next_queued_workspace_job`
    inside a short transaction instead.
    """
    stmt = (
        select(WorkspaceJob)
        .where(WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value)
        .order_by(WorkspaceJob.created_at.asc())
        .limit(1)
    )
    return session.exec(stmt).first()


def run_pending_jobs(
    session: Session,
    *,
    get_orchestrator: Callable[[Session, Workspace, WorkspaceJob], OrchestratorService],
    limit: int = 1,
) -> WorkspaceJobWorkerTickResult:
    """
    Process up to ``limit`` queued jobs. Each job uses a **fresh** session and **commit** so
    dequeue locking is correct and orchestrator adapters see the same session as persistence.

    ``session`` is only used for :meth:`~sqlmodel.Session.get_bind`; it is not mutated by this
    function. The caller does **not** need to commit ``session`` afterward for worker writes (an
    outer commit is a no-op if the session is clean).
    """
    bind = session.get_bind()
    sm = _worker_sessionmaker(bind)
    processed = 0
    last_id: int | None = None
    for _ in range(max(1, limit)):
        work = sm()
        try:
            jid = _process_next_queued_job_return_id(work, get_orchestrator)
            if jid is None:
                work.rollback()
                break
            work.commit()
            processed += 1
            last_id = jid
        except Exception:
            work.rollback()
            raise
        finally:
            work.close()
    return WorkspaceJobWorkerTickResult(processed_count=processed, last_job_id=last_id)


def run_one_pending_workspace_job(
    session: Session,
    *,
    get_orchestrator: Callable[[Session, Workspace, WorkspaceJob], OrchestratorService],
) -> WorkspaceJobWorkerTickResult:
    """Run at most one queued job; equivalent to ``run_pending_jobs(..., limit=1)``."""
    return run_pending_jobs(session, get_orchestrator=get_orchestrator, limit=1)


def run_queued_workspace_job_by_id(
    session: Session,
    *,
    get_orchestrator: Callable[[Session, Workspace, WorkspaceJob], OrchestratorService],
    workspace_job_id: int,
) -> WorkspaceJobWorkerTickResult:
    """
    Run a single job by primary key if it is ``QUEUED`` and claimable (``SKIP LOCKED``);
    otherwise no-op (``processed_count=0``).
    """
    bind = session.get_bind()
    sm = _worker_sessionmaker(bind)
    work = sm()
    try:
        job = try_claim_queued_workspace_job_by_id(work, workspace_job_id)
        if job is None:
            work.rollback()
            return WorkspaceJobWorkerTickResult(processed_count=0, last_job_id=None)
        jid = job.workspace_job_id
        assert jid is not None
        with correlation_scope(job.correlation_id):
            wid = job.workspace_id
            ws = work.get(Workspace, wid)
            if ws is None:
                _mark_job_failed(work, job, "Workspace row not found for job")
                work.commit()
                return WorkspaceJobWorkerTickResult(processed_count=1, last_job_id=jid)
            try:
                orch = get_orchestrator(work, ws, job)
            except PlacementError as e:
                if isinstance(e, NoSchedulableNodeError):
                    log_event(
                        logger,
                        LogEvent.PLACEMENT_NO_SCHEDULABLE_NODE,
                        correlation_id=job.correlation_id,
                        workspace_id=wid,
                        workspace_job_id=jid,
                        job_type=job.job_type,
                        detail=str(e)[:500],
                    )
                    try:
                        maybe_provision_on_no_schedulable_capacity(work)
                    except Exception:
                        logger.exception("autoscaler_provision_on_no_capacity_unexpected_error")
                logger.warning(
                    "workspace_job_placement_failed",
                    extra={
                        "workspace_id": wid,
                        "workspace_job_id": jid,
                        "job_type": job.job_type,
                        "error": str(e),
                    },
                )
                pr = "no_schedulable_node" if isinstance(e, NoSchedulableNodeError) else "placement"
                _fail_job_from_placement(work, ws, job, str(e), placement_reason=pr)
                work.commit()
                return WorkspaceJobWorkerTickResult(processed_count=1, last_job_id=jid)
            except AppOrchestratorBindingError as e:
                logger.warning(
                    "workspace_job_orchestrator_binding_failed",
                    extra={
                        "workspace_id": wid,
                        "workspace_job_id": jid,
                        "job_type": job.job_type,
                        "error": str(e),
                    },
                )
                _fail_job_from_orchestrator_binding(work, ws, job, str(e))
                work.commit()
                return WorkspaceJobWorkerTickResult(processed_count=1, last_job_id=jid)
            _process_claimed_running_job(work, orch, job)
            work.commit()
            return WorkspaceJobWorkerTickResult(processed_count=1, last_job_id=jid)
    except Exception:
        work.rollback()
        raise
    finally:
        work.close()


def poll_workspace_jobs_tick(
    bind: Engine,
    *,
    get_orchestrator: Callable[[Session, Workspace, WorkspaceJob], OrchestratorService],
    limit: int = 1,
) -> WorkspaceJobWorkerTickResult:
    """
    Process up to ``limit`` jobs using ``bind`` only (no caller-owned session).

    Suitable for a dedicated worker process; same dequeue semantics as :func:`run_pending_jobs`.
    """
    sm = _worker_sessionmaker(bind)
    holder = sm()

    try:
        return run_pending_jobs(holder, get_orchestrator=get_orchestrator, limit=limit)
    finally:
        holder.close()
