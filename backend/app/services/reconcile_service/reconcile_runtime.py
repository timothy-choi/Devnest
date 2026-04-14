"""Execute RECONCILE_RUNTIME jobs: conservative drift detection and safe repairs."""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent, log_event
from app.services.gateway_client.errors import GatewayClientError
from app.services.gateway_client.gateway_client import DevnestGatewayClient
from app.services.orchestrator_service.errors import (
    WorkspaceBringUpError,
    WorkspaceStopError,
)
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.reconcile_service.decisions import gateway_route_needs_repair, route_row_for_workspace
from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceStatus
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    record_workspace_event,
)
from app.workers.workspace_job_worker import worker as wmod
from app.workers.workspace_job_worker.failure_handling import (
    classify_reconcile_failure,
    try_schedule_workspace_job_retry,
)
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit

logger = logging.getLogger(__name__)

# TODO: distributed reconcile lease / leader election when multiple workers enqueue RECONCILE_RUNTIME.

_BUSY_RECONCILE = frozenset(
    {
        WorkspaceStatus.CREATING.value,
        WorkspaceStatus.STARTING.value,
        WorkspaceStatus.STOPPING.value,
        WorkspaceStatus.RESTARTING.value,
        WorkspaceStatus.UPDATING.value,
        WorkspaceStatus.DELETING.value,
    }
)

_ALLOWED_RECONCILE = frozenset(
    {
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
        WorkspaceStatus.DELETED.value,
    }
)


def _gateway_default_public_host(workspace_id: int, base_domain: str) -> str:
    dom = (base_domain or "app.devnest.local").strip().strip(".")
    return f"{workspace_id}.{dom}"


def _strict_list_routes() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        return []
    return DevnestGatewayClient.from_settings(settings).get_registered_routes()


def _strict_register_route(ws: Workspace, internal_endpoint: str) -> None:
    settings = get_settings()
    wid = ws.workspace_id
    assert wid is not None
    public = (ws.public_host or "").strip() or _gateway_default_public_host(
        int(wid),
        settings.devnest_base_domain,
    )
    DevnestGatewayClient.from_settings(settings).register_route(str(wid), internal_endpoint, public)


def _strict_deregister_route(workspace_id: int) -> None:
    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        return
    DevnestGatewayClient.from_settings(settings).deregister_route(str(workspace_id))


def _best_effort_remove_orphan_gateway_route(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    *,
    workspace_id: int,
    message: str,
) -> bool:
    """
    If route-admin still lists a route for this workspace, DELETE it.

    Used after stop finalization (worker deregister is best-effort) and for explicit orphan cleanup.
    Does not raise: logs and returns False on gateway errors so a succeeded job is not reversed.
    """
    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        return False
    try:
        routes = _strict_list_routes()
    except GatewayClientError as e:
        logger.warning(
            "reconcile_gateway_list_failed_best_effort",
            extra={"workspace_id": workspace_id, "error": str(e)},
        )
        return False
    if route_row_for_workspace(routes, workspace_id) is None:
        return False
    try:
        _strict_deregister_route(workspace_id)
    except GatewayClientError as e:
        logger.warning(
            "reconcile_gateway_deregister_failed_best_effort",
            extra={"workspace_id": workspace_id, "error": str(e)},
        )
        return False
    record_workspace_event(
        session,
        workspace_id=workspace_id,
        event_type=WorkspaceStreamEventType.RECONCILE_CLEANED_ORPHAN,
        status=ws.status,
        message=message,
        payload={"job_id": job.workspace_job_id},
    )
    return True


def _repair_runtime_capacity_ledger(session: Session, ws: Workspace) -> None:
    """
    Best-effort alignment of ``WorkspaceRuntime.reserved_*`` with workspace status (drift control).

    - ``STOPPED`` / ``ERROR``: reservations should be zero (capacity released for placement).
    - ``RUNNING`` with ``node_id`` but missing ledger: backfill defaults (legacy rows).
    """
    wid = ws.workspace_id
    assert wid is not None
    rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    if rt is None:
        return
    changed = False
    if ws.status in (WorkspaceStatus.STOPPED.value, WorkspaceStatus.ERROR.value):
        if float(rt.reserved_cpu or 0) > 0 or int(rt.reserved_memory_mb or 0) > 0:
            rt.reserved_cpu = 0.0
            rt.reserved_memory_mb = 0
            changed = True
    elif ws.status == WorkspaceStatus.RUNNING.value:
        nk = (rt.node_id or "").strip()
        if nk and float(rt.reserved_cpu or 0) <= 0 and int(rt.reserved_memory_mb or 0) <= 0:
            rt.reserved_cpu = float(DEFAULT_WORKSPACE_REQUEST_CPU)
            rt.reserved_memory_mb = int(DEFAULT_WORKSPACE_REQUEST_MEMORY_MB)
            changed = True
    if changed:
        rt.updated_at = wmod._now()
        session.add(rt)


def _runtime_snapshot(session: Session, workspace_id: int) -> tuple[str | None, str | None, str | None]:
    row = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)).first()
    if row is None:
        return (None, None, None)
    return (row.container_id, row.internal_endpoint, row.health_status)


def execute_reconcile_runtime_job(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
    wid = ws.workspace_id
    assert wid is not None
    wid_str = str(wid)
    cfg_v = int(job.requested_config_version)
    requested_by = str(job.requested_by_user_id)

    log_event(
        logger,
        LogEvent.RECONCILE_STARTED,
        correlation_id=job.correlation_id,
        workspace_id=wid,
        workspace_job_id=job.workspace_job_id,
        workspace_status=ws.status,
    )

    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.RECONCILE_STARTED,
        status=ws.status,
        message="Reconcile started",
        payload={
            "job_id": job.workspace_job_id,
            "job_type": job.job_type,
        },
    )

    record_audit(
        session,
        action=AuditAction.RECONCILE_STARTED.value,
        resource_type="workspace",
        resource_id=wid,
        actor_type=AuditActorType.INTERNAL_SERVICE.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=wid,
        job_id=job.workspace_job_id,
        correlation_id=job.correlation_id,
        metadata={"workspace_status": ws.status},
    )

    _repair_runtime_capacity_ledger(session, ws)

    if ws.status in _BUSY_RECONCILE:
        _fail_reconcile(session, ws, job, f"reconcile:workspace_busy (status={ws.status})")
        return

    if ws.status not in _ALLOWED_RECONCILE:
        _fail_reconcile(session, ws, job, f"reconcile:unsupported_workspace_status:{ws.status}")
        return

    if ws.status == WorkspaceStatus.DELETED.value:
        _reconcile_deleted(session, ws, job)
        return

    if ws.status == WorkspaceStatus.ERROR.value:
        _reconcile_error_cleanup(session, orchestrator, ws, job)
        return

    if ws.status == WorkspaceStatus.STOPPED.value:
        _reconcile_stopped(session, orchestrator, ws, job, requested_by=requested_by)
        return

    if ws.status == WorkspaceStatus.RUNNING.value:
        _reconcile_running(session, orchestrator, ws, job, config_version=cfg_v)
        return


def _reconcile_deleted(session: Session, ws: Workspace, job: WorkspaceJob) -> None:
    wid = ws.workspace_id
    assert wid is not None
    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status=ws.status,
            message="Reconcile noop (gateway disabled)",
            payload={"job_id": job.workspace_job_id},
        )
        wmod._mark_job_succeeded(session, job)
        return
    try:
        routes = _strict_list_routes()
    except GatewayClientError as e:
        _fail_reconcile(session, ws, job, f"reconcile:gateway_list_failed:{e}")
        return
    row = route_row_for_workspace(routes, wid)
    if row is None:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status=ws.status,
            message="Reconcile noop (no gateway route for deleted workspace)",
            payload={"job_id": job.workspace_job_id},
        )
        wmod._mark_job_succeeded(session, job)
        return
    try:
        _strict_deregister_route(wid)
    except GatewayClientError as e:
        _fail_reconcile(session, ws, job, f"reconcile:gateway_deregister_failed:{e}")
        return
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.RECONCILE_CLEANED_ORPHAN,
        status=ws.status,
        message="Removed orphan gateway route for deleted workspace",
        payload={"job_id": job.workspace_job_id},
    )
    wmod._mark_job_succeeded(session, job)


def _reconcile_error_cleanup(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
    """Best-effort: orphan Docker + topology IP on the execution node, then orphan gateway routes."""
    wid = ws.workspace_id
    assert wid is not None
    persisted_container_id = wmod._get_persisted_container_id(session, wid)

    try:
        stop_res = orchestrator.stop_workspace_runtime(
            workspace_id=str(wid),
            container_id=persisted_container_id,
            release_ip_lease=True,
        )
    except WorkspaceStopError as e:
        _fail_reconcile(session, ws, job, f"reconcile:error_cleanup_stop_failed:{e}")
        return

    if stop_res.success:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_FIXED_RUNTIME,
            status=ws.status,
            message="Reconcile: cleaned orphan workspace engine/topology lease for ERROR workspace",
            payload={"job_id": job.workspace_job_id},
        )
    else:
        logger.info(
            "reconcile_error_engine_cleanup_partial",
            extra={
                "workspace_id": wid,
                "issues": (stop_res.issues or [])[:5],
            },
        )

    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status=ws.status,
            message="Reconcile noop (gateway disabled; engine cleanup attempted)",
            payload={"job_id": job.workspace_job_id},
        )
        wmod._mark_job_succeeded(session, job)
        return
    try:
        routes = _strict_list_routes()
    except GatewayClientError as e:
        _fail_reconcile(session, ws, job, f"reconcile:gateway_list_failed:{e}")
        return
    row = route_row_for_workspace(routes, wid)
    if row is None:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status=ws.status,
            message="Reconcile noop (no orphan gateway route after engine cleanup)",
            payload={"job_id": job.workspace_job_id},
        )
        wmod._mark_job_succeeded(session, job)
        return
    try:
        _strict_deregister_route(wid)
    except GatewayClientError as e:
        _fail_reconcile(session, ws, job, f"reconcile:gateway_deregister_failed:{e}")
        return
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.RECONCILE_CLEANED_ORPHAN,
        status=ws.status,
        message="Removed orphan gateway route while workspace in ERROR",
        payload={"job_id": job.workspace_job_id},
    )
    wmod._mark_job_succeeded(session, job)


def _fail_reconcile(session: Session, ws: Workspace, job: WorkspaceJob, message: str) -> None:
    """Mark reconcile job failed, or re-queue transient failures (bounded by ``WorkspaceJob.max_attempts``).

    ``STOPPED`` / ``ERROR`` / ``DELETED``: job failed only (workspace status unchanged).

    ``RUNNING`` (and any other non-terminal): workspace moves to ``ERROR`` via worker finalizer.

    Stream events for terminal failure are emitted in :func:`wmod._emit_job_outcome_event` (JOB_FAILED +
    RECONCILE_FAILED_TERMINAL for reconcile rows).
    """
    wid = ws.workspace_id
    assert wid is not None
    stage, retryable = classify_reconcile_failure(message)
    scheduled = bool(
        retryable
        and try_schedule_workspace_job_retry(
            session,
            job,
            message=message,
            stage=stage,
            failure_code=stage.value,
            truncate_message=wmod._truncate,
            now=wmod._now(),
        ),
    )
    if scheduled:
        wmod._touch_workspace(session, ws)
        return
    log_event(
        logger,
        LogEvent.RECONCILE_FAILED,
        level=logging.WARNING,
        correlation_id=job.correlation_id,
        workspace_id=wid,
        workspace_job_id=job.workspace_job_id,
        message=message[:500],
        failure_stage=stage.value,
        retryable=False,
    )
    record_audit(
        session,
        action=AuditAction.RECONCILE_FAILED.value,
        resource_type="workspace",
        resource_id=wid,
        actor_type=AuditActorType.INTERNAL_SERVICE.value,
        outcome=AuditOutcome.FAILURE.value,
        workspace_id=wid,
        job_id=job.workspace_job_id,
        correlation_id=job.correlation_id,
        reason=message[:4096],
        metadata={"failure_stage": stage.value, "workspace_status": ws.status},
    )
    if ws.status in (
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
        WorkspaceStatus.DELETED.value,
    ):
        wmod._mark_job_failed(
            session,
            job,
            message,
            failure_stage=stage.value,
            failure_code=stage.value,
        )
        wmod._touch_workspace(session, ws)
        return
    wmod._finalize_job_failed_workspace_error(session, ws, job, message=message, failure_stage=stage)


def _reconcile_stopped(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
    *,
    requested_by: str,
) -> None:
    wid = ws.workspace_id
    assert wid is not None
    persisted_container_id = wmod._get_persisted_container_id(session, wid)
    try:
        health = orchestrator.check_workspace_runtime_health(
            workspace_id=str(wid),
            container_id=persisted_container_id,
        )
    except WorkspaceBringUpError as e:
        _fail_reconcile(session, ws, job, f"reconcile:health_check_failed:{e}")
        return

    if health.success:
        try:
            stop_res = orchestrator.stop_workspace_runtime(
                workspace_id=str(wid),
                container_id=persisted_container_id,
                requested_by=requested_by,
            )
        except WorkspaceStopError as e:
            _fail_reconcile(session, ws, job, f"reconcile:stop_failed:{e}")
            return
        wmod._finalize_stop_result(session, ws, job, stop_res)
        # Worker session uses autoflush=False; refresh would load pre-finalize rows and clobber job status.
        if job.status != WorkspaceJobStatus.SUCCEEDED.value:
            record_workspace_event(
                session,
                workspace_id=wid,
                event_type=WorkspaceStreamEventType.RECONCILE_FAILED,
                status=ws.status,
                message=(job.error_msg or "reconcile:stop_finalize_failed"),
                payload={"job_id": job.workspace_job_id},
            )
            return
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_FIXED_RUNTIME,
            status=ws.status,
            message="Stopped lingering runtime while workspace was STOPPED",
            payload={"job_id": job.workspace_job_id},
        )
        _best_effort_remove_orphan_gateway_route(
            session,
            ws,
            job,
            workspace_id=wid,
            message="Removed orphan gateway route after stop reconcile (strict cleanup)",
        )
        return

    fixed_route = False
    settings = get_settings()
    if settings.devnest_gateway_enabled:
        try:
            routes = _strict_list_routes()
        except GatewayClientError as e:
            _fail_reconcile(session, ws, job, f"reconcile:gateway_list_failed:{e}")
            return
        row = route_row_for_workspace(routes, wid)
        if row is not None:
            try:
                _strict_deregister_route(wid)
            except GatewayClientError as e:
                _fail_reconcile(session, ws, job, f"reconcile:gateway_deregister_failed:{e}")
                return
            fixed_route = True
            record_workspace_event(
                session,
                workspace_id=wid,
                event_type=WorkspaceStreamEventType.RECONCILE_CLEANED_ORPHAN,
                status=ws.status,
                message="Removed orphan gateway route while workspace was STOPPED",
                payload={"job_id": job.workspace_job_id},
            )

    if not fixed_route:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status=ws.status,
            message="Reconcile noop (already aligned with STOPPED)",
            payload={"job_id": job.workspace_job_id},
        )
    wmod._mark_job_succeeded(session, job)


def _reconcile_running(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
    *,
    config_version: int,
) -> None:
    wid = ws.workspace_id
    assert wid is not None
    persisted_container_id = wmod._get_persisted_container_id(session, wid)

    try:
        health = orchestrator.check_workspace_runtime_health(
            workspace_id=str(wid),
            container_id=persisted_container_id,
        )
    except WorkspaceBringUpError as e:
        _fail_reconcile(session, ws, job, f"reconcile:health_check_failed:{e}")
        return

    if not health.success:
        msg = wmod._format_issues(health.issues) or "reconcile:runtime_not_healthy"
        _fail_reconcile(session, ws, job, msg)
        return

    before = _runtime_snapshot(session, wid)
    # ``requested_config_version`` is frozen at enqueue time (matches other job types).
    wmod._apply_runtime_bringup_like(
        session,
        wid,
        node_id=health.node_id,
        topology_id=health.topology_id,
        container_id=health.container_id,
        container_state=health.container_state,
        internal_endpoint=health.internal_endpoint,
        config_version=config_version,
        probe_healthy=health.probe_healthy,
    )
    after = _runtime_snapshot(session, wid)
    runtime_changed = before != after
    if health.internal_endpoint:
        ws.endpoint_ref = health.internal_endpoint
    wmod._touch_workspace(session, ws)

    if runtime_changed:
        log_event(
            logger,
            LogEvent.RECONCILE_FIXED_RUNTIME,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=job.workspace_job_id,
        )
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_FIXED_RUNTIME,
            status=ws.status,
            message="Synced WorkspaceRuntime from observed health",
            payload={"job_id": job.workspace_job_id},
        )

    settings = get_settings()
    fixed_route = False
    if settings.devnest_gateway_enabled and health.internal_endpoint:
        try:
            routes = _strict_list_routes()
        except GatewayClientError as e:
            _fail_reconcile(session, ws, job, f"reconcile:gateway_list_failed:{e}")
            return
        row = route_row_for_workspace(routes, wid)
        if gateway_route_needs_repair(
            route_row=row,
            observed_internal_endpoint=health.internal_endpoint,
        ):
            try:
                _strict_register_route(ws, health.internal_endpoint)
            except GatewayClientError as e:
                _fail_reconcile(session, ws, job, f"reconcile:gateway_register_failed:{e}")
                return
            fixed_route = True
            log_event(
                logger,
                LogEvent.RECONCILE_FIXED_ROUTE,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_job_id=job.workspace_job_id,
            )
            record_workspace_event(
                session,
                workspace_id=wid,
                event_type=WorkspaceStreamEventType.RECONCILE_FIXED_ROUTE,
                status=ws.status,
                message="Re-registered gateway route",
                payload={"job_id": job.workspace_job_id},
            )

    if not runtime_changed and not fixed_route:
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status=ws.status,
            message="Reconcile noop (runtime and gateway already aligned)",
            payload={"job_id": job.workspace_job_id},
        )

    wmod._mark_job_succeeded(session, job)
