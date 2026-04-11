"""DB-backed workspace job executor: dequeue ``QUEUED`` jobs, call orchestrator, persist outcomes.

V1: sequential processing, no row locks, no distributed lease. Multiple pollers may double-process
the same job; production hardening should add ``FOR UPDATE SKIP LOCKED`` or equivalent.

**Persistence:** The worker is the system of record for :class:`~app.services.workspace_service.models.Workspace`,
:class:`~app.services.workspace_service.models.WorkspaceJob`, and
:class:`~app.services.workspace_service.models.WorkspaceRuntime` after orchestration. The orchestrator
returns result DTOs only; this module maps them onto ORM rows and emits workspace stream events.

Gateway registration, SSE transport, reconcileRuntime, and EC2/scheduler integrations are intentionally
out of scope (see TODOs in orchestrator).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

logger = logging.getLogger(__name__)

from app.services.orchestrator_service.errors import (
    WorkspaceBringUpError,
    WorkspaceDeleteError,
    WorkspaceRestartError,
    WorkspaceStopError,
    WorkspaceUpdateError,
)
from app.services.orchestrator_service.interfaces import OrchestratorService
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


def _mark_job_failed(session: Session, job: WorkspaceJob, message: str | None) -> None:
    job.status = WorkspaceJobStatus.FAILED.value
    job.finished_at = _now()
    job.error_msg = _truncate(message, 8192)
    session.add(job)


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
    )
    _mark_job_succeeded(session, job)
    ws.status = WorkspaceStatus.RUNNING.value
    _workspace_clear_errors(ws)
    ws.endpoint_ref = internal_endpoint or ws.endpoint_ref
    ws.last_started = _now()
    _touch_workspace(session, ws)


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
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
            status=ws.status,
            message="Workspace job succeeded",
            payload=base_payload,
        )
    elif job.status == WorkspaceJobStatus.FAILED.value:
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


def _process_one_job(session: Session, orchestrator: OrchestratorService, job: WorkspaceJob) -> None:
    """
    Run a single queued job: transition to ``RUNNING``, call orchestrator, persist outcomes, emit events.

    Commits are owned by the caller (e.g. :func:`execute_workspace_job_tick`).
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
        _mark_job_running(session, job)
        _mark_job_failed(session, job, "Workspace row not found for job")
        return

    logger.info(
        "workspace_job_started",
        extra={
            "workspace_id": wid,
            "workspace_job_id": jid,
            "job_type": jt,
            "attempt": int(job.attempt or 0) + 1,
        },
    )
    _mark_job_running(session, job)
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
    except UnsupportedWorkspaceJobTypeError as e:
        logger.error(
            "workspace_job_unsupported_type",
            extra={"workspace_id": wid, "workspace_job_id": jid, "job_type": jt, "error": str(e)},
        )
        _mark_job_failed(session, job, str(e))
        ws.status = WorkspaceStatus.ERROR.value
        _workspace_set_error(ws, _ERROR_CODE_JOB, str(e))
        _touch_workspace(session, ws)

    logger.info(
        "workspace_job_finished",
        extra={
            "workspace_id": wid,
            "workspace_job_id": jid,
            "job_type": jt,
            "job_status": job.status,
            "workspace_status": ws.status,
        },
    )
    _emit_job_outcome_event(session, wid=wid, ws=ws, job=job)


def load_next_queued_workspace_job(session: Session) -> WorkspaceJob | None:
    """Return the oldest ``QUEUED`` workspace job, or ``None``."""
    stmt = (
        select(WorkspaceJob)
        .where(WorkspaceJob.status == WorkspaceJobStatus.QUEUED.value)
        .order_by(WorkspaceJob.created_at.asc())
        .limit(1)
    )
    return session.exec(stmt).first()


def run_pending_jobs(
    session: Session,
    orchestrator: OrchestratorService,
    *,
    limit: int = 1,
) -> WorkspaceJobWorkerTickResult:
    """
    Process up to ``limit`` queued jobs sequentially in the current session.

    Does not commit; callers should ``session.commit()`` after this returns successfully or
    ``session.rollback()`` on failure.
    """
    processed = 0
    last_id: int | None = None
    for _ in range(max(1, limit)):
        job = load_next_queued_workspace_job(session)
        if job is None:
            break
        last_id = job.workspace_job_id
        _process_one_job(session, orchestrator, job)
        session.flush()
        processed += 1
    return WorkspaceJobWorkerTickResult(processed_count=processed, last_job_id=last_id)


def run_one_pending_workspace_job(
    session: Session,
    orchestrator: OrchestratorService,
) -> WorkspaceJobWorkerTickResult:
    """Run at most one queued job; equivalent to ``run_pending_jobs(..., limit=1)``."""
    return run_pending_jobs(session, orchestrator, limit=1)


def run_queued_workspace_job_by_id(
    session: Session,
    orchestrator: OrchestratorService,
    *,
    workspace_job_id: int,
) -> WorkspaceJobWorkerTickResult:
    """
    Run a single job by primary key if it is ``QUEUED``; otherwise no-op (``processed_count=0``).

    Does not commit; callers should ``session.commit()`` after success or ``rollback`` on failure.
    """
    job = session.get(WorkspaceJob, workspace_job_id)
    if job is None:
        return WorkspaceJobWorkerTickResult(processed_count=0, last_job_id=None)
    if job.status != WorkspaceJobStatus.QUEUED.value:
        return WorkspaceJobWorkerTickResult(processed_count=0, last_job_id=None)
    jid = job.workspace_job_id
    _process_one_job(session, orchestrator, job)
    session.flush()
    return WorkspaceJobWorkerTickResult(processed_count=1, last_job_id=jid)
