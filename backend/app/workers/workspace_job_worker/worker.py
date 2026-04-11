"""DB-backed workspace job executor: dequeue ``QUEUED`` jobs, call orchestrator, persist outcomes.

V1: sequential processing, no row locks, no distributed lease. Multiple pollers may double-process
the same job; production hardening should add ``FOR UPDATE SKIP LOCKED`` or equivalent.

Gateway registration, SSE, reconcileRuntime, and EC2/scheduler integrations are intentionally
out of scope (see TODOs in orchestrator).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

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
        _apply_runtime_bringup_like(
            session,
            wid,
            node_id=result.node_id,
            topology_id=result.topology_id,
            container_id=result.container_id,
            container_state=result.container_state,
            internal_endpoint=result.internal_endpoint,
            config_version=config_version,
            probe_healthy=result.probe_healthy,
        )
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.RUNNING.value
        _workspace_clear_errors(ws)
        ws.endpoint_ref = result.internal_endpoint or ws.endpoint_ref
        ws.last_started = _now()
        _touch_workspace(session, ws)
        return

    msg = _format_issues(result.issues) or "Bring-up completed without success"
    _mark_job_failed(session, job, msg)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, msg)
    _touch_workspace(session, ws)


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
    _mark_job_failed(session, job, msg)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, msg)
    _touch_workspace(session, ws)


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
    _mark_job_failed(session, job, msg)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, msg)
    _touch_workspace(session, ws)


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
        _apply_runtime_bringup_like(
            session,
            wid,
            node_id=result.node_id,
            topology_id=result.topology_id,
            container_id=result.container_id,
            container_state=result.container_state,
            internal_endpoint=result.internal_endpoint,
            config_version=config_version,
            probe_healthy=result.probe_healthy,
        )
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.RUNNING.value
        _workspace_clear_errors(ws)
        ws.endpoint_ref = result.internal_endpoint or ws.endpoint_ref
        ws.last_started = _now()
        _touch_workspace(session, ws)
        return

    msg = _format_issues(result.issues) or "Restart completed without success"
    _mark_job_failed(session, job, msg)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, msg)
    _touch_workspace(session, ws)


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
        _apply_runtime_bringup_like(
            session,
            wid,
            node_id=result.node_id,
            topology_id=result.topology_id,
            container_id=result.container_id,
            container_state=result.container_state,
            internal_endpoint=result.internal_endpoint,
            config_version=cfg_v,
            probe_healthy=result.probe_healthy,
        )
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.RUNNING.value
        _workspace_clear_errors(ws)
        ws.endpoint_ref = result.internal_endpoint or ws.endpoint_ref
        ws.last_started = _now()
        _touch_workspace(session, ws)
        return

    msg = _format_issues(result.issues) or "Update completed without success"
    _mark_job_failed(session, job, msg)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, msg)
    _touch_workspace(session, ws)


def _execute_job_body(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
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


def _process_one_job(session: Session, orchestrator: OrchestratorService, job: WorkspaceJob) -> None:
    wid = job.workspace_id
    ws = session.get(Workspace, wid)
    if ws is None:
        _mark_job_running(session, job)
        _mark_job_failed(session, job, "Workspace row not found for job")
        return

    _mark_job_running(session, job)

    try:
        _execute_job_body(session, orchestrator, ws, job)
    except _ORCHESTRATOR_EXCEPTIONS as e:
        _mark_job_failed(session, job, str(e))
        ws.status = WorkspaceStatus.ERROR.value
        _workspace_set_error(ws, _ERROR_CODE_ORCH, str(e))
        _touch_workspace(session, ws)
    except UnsupportedWorkspaceJobTypeError as e:
        _mark_job_failed(session, job, str(e))
        ws.status = WorkspaceStatus.ERROR.value
        _workspace_set_error(ws, _ERROR_CODE_JOB, str(e))
        _touch_workspace(session, ws)


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
