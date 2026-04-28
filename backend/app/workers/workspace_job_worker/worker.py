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
from urllib.parse import urlsplit

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
    WorkspaceSnapshotError,
    WorkspaceStopError,
    WorkspaceUpdateError,
)
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.autoscaler_service.service import maybe_provision_on_no_schedulable_capacity
from app.services.cleanup_service import (
    CLEANUP_SCOPE_BRINGUP_ROLLBACK,
    CLEANUP_SCOPE_STOP_INCOMPLETE,
    ensure_durable_cleanup_task,
)
from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_DISK_MB,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.placement_service.errors import NoSchedulableNodeError, PlacementError
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
)
from app.services.placement_service.node_heartbeat import try_emit_default_local_execution_node_heartbeat
from app.services.gateway_client.gateway_client import DevnestGatewayClient
from app.services.gateway_client.workspace_route_upstream import traefik_upstream_for_workspace_gateway
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceRestartResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)
from app.services.storage.factory import get_snapshot_storage_provider
from app.services.workspace_service.api.schemas.workspace_schemas import get_workspace_features
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceConfig,
    WorkspaceJob,
    WorkspaceRuntime,
    WorkspaceSnapshot,
)
from app.services.workspace_service.services.workspace_secret_service import (
    resolve_workspace_runtime_secret_env,
)
from app.services.notification_service.services.workspace_lifecycle_notifications import (
    maybe_emit_workspace_lifecycle_notification,
)
from app.services.workspace_service.models.enums import (
    FailureStage,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntimeHealthStatus,
    WorkspaceSnapshotStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    record_workspace_event,
)
from app.services.workspace_service.services.workspace_session_service import revoke_all_workspace_sessions
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.service import record_usage

from app.libs.common.config import get_settings
from app.libs.observability.correlation import correlation_scope
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability import metrics as devnest_metrics

from .errors import UnsupportedWorkspaceJobTypeError
from .failure_handling import (
    classify_placement_error,
    effective_max_attempts,
    lifecycle_result_failure_retryable,
    orchestrator_binding_retryable,
    orchestrator_exception_retryable,
    queued_job_eligible_where,
    try_schedule_workspace_job_retry,
)
from .results import WorkspaceJobWorkerTickResult

_ORCHESTRATOR_EXCEPTIONS: tuple[type[Exception], ...] = (
    WorkspaceBringUpError,
    WorkspaceStopError,
    WorkspaceDeleteError,
    WorkspaceRestartError,
    WorkspaceUpdateError,
    WorkspaceSnapshotError,
)

_ERROR_CODE_JOB = "WORKSPACE_JOB_FAILED"
_ERROR_CODE_ORCH = "ORCHESTRATOR_EXCEPTION"
_ERROR_CODE_PLACEMENT = "PLACEMENT_FAILED"
_ERROR_CODE_ORCHESTRATOR_BINDING = "ORCHESTRATOR_BINDING_FAILED"
_REMOTE_EXECUTION_MODES = frozenset(
    {
        ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ExecutionNodeExecutionMode.SSH_DOCKER.value,
    }
)


def _clear_runtime_capacity_reservation(session: Session, workspace_id: int) -> None:
    """Zero ``WorkspaceRuntime.reserved_*`` when workspace moves to a non-scheduling terminal error path."""
    rt = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    if rt is None:
        return
    if (
        float(rt.reserved_cpu or 0) <= 0
        and int(rt.reserved_memory_mb or 0) <= 0
        and int(rt.reserved_disk_mb or 0) <= 0
    ):
        return
    rt.reserved_cpu = 0.0
    rt.reserved_memory_mb = 0
    rt.reserved_disk_mb = 0
    rt.updated_at = _now()
    session.add(rt)


def _fail_job_from_placement(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    exc: BaseException,
    *,
    placement_reason: str = "placement",
) -> None:
    message = str(exc)
    stage, _ = classify_placement_error(exc)
    if try_schedule_workspace_job_retry(
        session,
        job,
        message=message,
        stage=stage,
        failure_code=placement_reason,
        truncate_message=_truncate,
        now=_now(),
    ):
        _touch_workspace(session, ws)
        return
    devnest_metrics.record_placement_failure(reason=placement_reason)
    _mark_job_failed(session, job, message, failure_stage=stage.value, failure_code=placement_reason)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_PLACEMENT, message)
    _touch_workspace(session, ws)
    assert ws.workspace_id is not None
    _clear_runtime_capacity_reservation(session, ws.workspace_id)


def _fail_job_from_orchestrator_binding(session: Session, ws: Workspace, job: WorkspaceJob, exc: BaseException) -> None:
    """Node execution / Docker / SSH binding failed before orchestrator could run the job."""
    message = str(exc)
    stage, _ = orchestrator_binding_retryable()
    if try_schedule_workspace_job_retry(
        session,
        job,
        message=message,
        stage=stage,
        failure_code="orchestrator_binding",
        truncate_message=_truncate,
        now=_now(),
    ):
        _touch_workspace(session, ws)
        return
    devnest_metrics.record_placement_failure(reason="orchestrator_binding")
    _mark_job_failed(session, job, message, failure_stage=stage.value, failure_code="orchestrator_binding")
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
    now = _now()
    return (
        select(WorkspaceJob)
        .where(queued_job_eligible_where(WorkspaceJob, now))
        .order_by(WorkspaceJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _stmt_queued_job_by_id_for_update(workspace_job_id: int):
    now = _now()
    return (
        select(WorkspaceJob)
        .where(
            WorkspaceJob.workspace_job_id == workspace_job_id,
            queued_job_eligible_where(WorkspaceJob, now),
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


def _get_persisted_container_id(session: Session, workspace_id: int) -> str | None:
    """Return ``WorkspaceRuntime.container_id`` for the given workspace if available."""
    rt = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    return rt.container_id if rt is not None else None


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
    gateway_route_target: str | None = None,
    reserved_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    reserved_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
    reserved_disk_mb: int = DEFAULT_WORKSPACE_REQUEST_DISK_MB,
) -> None:
    """Persist placement + health snapshot after a successful bring-up / restart / update (running)."""
    rt = _get_or_create_runtime(session, workspace_id)
    ts = _now()
    rt.node_id = node_id
    rt.topology_id = _parse_topology_id(topology_id)
    rt.container_id = container_id
    rt.container_state = container_state
    rt.internal_endpoint = internal_endpoint
    rt.gateway_route_target = gateway_route_target
    rt.config_version = config_version
    nk = (node_id or "").strip()
    if nk:
        rt.reserved_cpu = float(reserved_cpu)
        rt.reserved_memory_mb = int(reserved_memory_mb)
        rt.reserved_disk_mb = int(reserved_disk_mb)
    else:
        rt.reserved_cpu = 0.0
        rt.reserved_memory_mb = 0
        rt.reserved_disk_mb = 0
    rt.health_status = _health_from_probe(probe_healthy)
    if probe_healthy is True:
        rt.last_heartbeat_at = ts
    rt.updated_at = ts
    session.add(rt)


def _sync_runtime_after_failed_bringup(session: Session, workspace_id: int, result: WorkspaceBringUpResult) -> None:
    """Align ``WorkspaceRuntime`` with post-rollback reality after a failed bring-up result."""
    if not result.rollback_attempted:
        return
    rt = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    if rt is None:
        return
    ts = _now()
    if result.rollback_succeeded:
        if result.container_id is not None:
            rt.container_id = result.container_id
        if result.container_state is not None:
            rt.container_state = result.container_state
        rt.internal_endpoint = result.internal_endpoint
        rt.gateway_route_target = result.gateway_route_target
        rt.reserved_cpu = 0.0
        rt.reserved_memory_mb = 0
        rt.reserved_disk_mb = 0
        rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
    else:
        rt.health_status = WorkspaceRuntimeHealthStatus.CLEANUP_REQUIRED.value
        ensure_durable_cleanup_task(
            session,
            workspace_id=workspace_id,
            scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK,
            detail=list(result.rollback_issues or result.issues or []),
        )
    rt.updated_at = ts
    session.add(rt)


def _sync_runtime_after_bringup_exception(session: Session, workspace_id: int, exc: WorkspaceBringUpError) -> None:
    """Persist rollback outcome when bring-up raised after compensating stop was attempted."""
    if not exc.rollback_attempted:
        return
    rt = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id),
    ).first()
    if rt is None:
        return
    ts = _now()
    if exc.rollback_succeeded:
        if exc.rollback_container_id is not None:
            rt.container_id = exc.rollback_container_id
        if exc.rollback_container_state is not None:
            rt.container_state = exc.rollback_container_state
        rt.internal_endpoint = None
        rt.gateway_route_target = None
        rt.reserved_cpu = 0.0
        rt.reserved_memory_mb = 0
        rt.reserved_disk_mb = 0
        rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
    else:
        rt.health_status = WorkspaceRuntimeHealthStatus.CLEANUP_REQUIRED.value
        ensure_durable_cleanup_task(
            session,
            workspace_id=workspace_id,
            scope=CLEANUP_SCOPE_BRINGUP_ROLLBACK,
            detail=list(exc.rollback_issues or []),
        )
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
    if result.topology_detached is not False:
        rt.topology_id = None
    rt.internal_endpoint = None
    rt.gateway_route_target = None
    rt.reserved_cpu = 0.0
    rt.reserved_memory_mb = 0
    rt.reserved_disk_mb = 0
    rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
    rt.last_heartbeat_at = None
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
    row.node_id = None
    row.topology_id = None
    row.container_id = None
    row.container_state = "deleted"
    row.internal_endpoint = None
    row.gateway_route_target = None
    row.reserved_cpu = 0.0
    row.reserved_memory_mb = 0
    row.reserved_disk_mb = 0
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
    job.failure_stage = None
    job.failure_code = None
    job.next_attempt_after = None
    session.add(job)
    devnest_metrics.record_job_terminal(
        job_type=job.job_type or "unknown",
        status=WorkspaceJobStatus.SUCCEEDED.value,
    )
    if job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value:
        devnest_metrics.record_reconcile_terminal(succeeded=True)


def _mark_job_failed(
    session: Session,
    job: WorkspaceJob,
    message: str | None,
    *,
    failure_stage: str | None = None,
    failure_code: str | None = None,
) -> None:
    job.status = WorkspaceJobStatus.FAILED.value
    job.finished_at = _now()
    job.error_msg = _truncate(message, 8192)
    job.failure_stage = failure_stage
    job.failure_code = failure_code or failure_stage
    job.next_attempt_after = None
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
    wid = ws.workspace_id
    if wid is not None:
        log_event(
            logger,
            LogEvent.WORKSPACE_STATUS_ERROR,
            workspace_id=int(wid),
            error_code=_truncate(code, 64),
            detail=_truncate(message, 256),
        )


def _touch_workspace(session: Session, ws: Workspace) -> None:
    ws.updated_at = _now()
    session.add(ws)


def _route_target_port(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    try:
        if parsed.port is not None:
            return int(parsed.port)
    except ValueError:
        return None
    host_port = (parsed.netloc or parsed.path or "").rsplit(":", 1)
    if len(host_port) == 2 and host_port[1].isdigit():
        return int(host_port[1])
    return None


def _remote_gateway_route_target_for_node(
    session: Session,
    *,
    workspace_id: int | None = None,
    node_key: str | None,
    execution_node_id: int | None = None,
    gateway_route_target: str | None,
    internal_endpoint: str | None,
) -> str | None:
    """
    For EC2/remote Docker nodes, Traefik must reach the execution host's published port.

    The orchestrator may still report ``internal_endpoint=127.0.0.1:<published_port>`` because
    probes run on the execution host via SSM/SSH. Convert that to
    ``http://{execution_node.private_ip}:{published_port}`` for route-admin.
    """
    key = (node_key or "").strip()
    node: ExecutionNode | None = None
    if key:
        node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if node is None and execution_node_id is not None:
        node = session.get(ExecutionNode, int(execution_node_id))
    if node is None:
        return (gateway_route_target or "").strip() or None

    execution_mode = (node.execution_mode or "").strip()
    provider_type = (node.provider_type or "").strip()
    is_remote = provider_type == ExecutionNodeProviderType.EC2.value or execution_mode in _REMOTE_EXECUTION_MODES
    if not is_remote:
        return (gateway_route_target or "").strip() or None

    private_ip = (node.private_ip or "").strip()
    if not private_ip:
        return (gateway_route_target or "").strip() or None
    port = _route_target_port(gateway_route_target) or _route_target_port(internal_endpoint)
    if port is None:
        return (gateway_route_target or "").strip() or None
    selected = f"{private_ip}:{port}"
    logger.info(
        "workspace_remote_route_target_selected",
        extra={
            "workspace_id": workspace_id,
            "node_key": (node.node_key or key or None),
            "execution_mode": execution_mode,
            "private_ip": private_ip,
            "published_port": port,
            "gateway_route_target": selected,
        },
    )
    return selected


def _gateway_default_public_host(workspace_id: int, base_domain: str) -> str:
    dom = (base_domain or "app.devnest.local").strip().strip(".")
    # Must match ``GET /internal/gateway/auth`` host parsing (``ws-{id}.<base_domain>``).
    return f"ws-{workspace_id}.{dom}"


def _gateway_try_register_running(session: Session, ws: Workspace) -> None:
    """Notify route-admin after RUNNING; failures are logged only (control plane stays authoritative)."""
    try:
        settings = get_settings()
        if not settings.devnest_gateway_enabled:
            return
        wid = ws.workspace_id
        if wid is None:
            return
        rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
        if rt is None:
            return
        upstream = traefik_upstream_for_workspace_gateway(ws, rt)
        if not upstream:
            logger.debug(
                "gateway_register_skipped_no_upstream",
                extra={"workspace_id": wid},
            )
            return
        public = (ws.public_host or "").strip() or _gateway_default_public_host(
            int(wid),
            settings.devnest_base_domain,
        )
        nk = ((rt.node_id or "").strip() or None)
        node = None
        if nk:
            node = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == nk)).first()
        execution_mode = (getattr(node, "execution_mode", None) or "").strip() if node is not None else None
        provider_type = (getattr(node, "provider_type", None) or "").strip() if node is not None else None
        topology_skipped_for_remote = bool(
            provider_type == ExecutionNodeProviderType.EC2.value or execution_mode in _REMOTE_EXECUTION_MODES
        )
        logger.info(
            "gateway_route_register_attempt",
            extra={
                "workspace_id": wid,
                "public_host": public,
                "node_key": nk,
                "execution_mode": execution_mode,
                "execution_node_id": ws.execution_node_id,
                "gateway_route_target": upstream,
                "gateway_upstream_target": upstream,
                "topology_skipped_for_remote": topology_skipped_for_remote,
            },
        )
        if topology_skipped_for_remote:
            logger.info(
                "remote_topology_route_target_selected",
                extra={
                    "workspace_id": wid,
                    "node_key": nk,
                    "execution_mode": execution_mode,
                    "gateway_route_target": upstream,
                    "topology_skipped_for_remote": True,
                },
            )
        DevnestGatewayClient.from_settings(settings).register_route(
            str(wid),
            upstream,
            public,
            node_key=nk,
            execution_node_id=ws.execution_node_id,
        )
    except Exception as e:
        logger.warning(
            "gateway_register_failed_best_effort",
            extra={"workspace_id": getattr(ws, "workspace_id", None), "error": str(e)},
        )


def _gateway_route_telemetry_before_runtime_clear(session: Session, ws: Workspace, wid: int) -> dict[str, str | None]:
    """Snapshot ``public_host`` / ``node_key`` / upstream for deregister logs (Step 9)."""
    rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    settings = get_settings()
    public = (ws.public_host or "").strip() or _gateway_default_public_host(
        int(wid),
        settings.devnest_base_domain,
    )
    nk = ((rt.node_id or "").strip() if rt else None) or None
    if rt is None:
        up = (ws.endpoint_ref or "").strip() or None
    else:
        up = traefik_upstream_for_workspace_gateway(ws, rt) or None
    return {"public_host": public, "node_key": nk, "gateway_upstream_target": up}


def _gateway_try_deregister(
    workspace_id: int,
    *,
    public_host: str | None = None,
    node_key: str | None = None,
    gateway_upstream_target: str | None = None,
) -> None:
    """Remove route on stop/delete; failures are logged only."""
    try:
        settings = get_settings()
        if not settings.devnest_gateway_enabled:
            return
        DevnestGatewayClient.from_settings(settings).deregister_route(
            str(workspace_id),
            public_host=public_host,
            node_key=node_key,
            gateway_upstream_target=gateway_upstream_target,
        )
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
    failure_stage: FailureStage | None = None,
) -> None:
    """Mark job failed, move workspace to ``ERROR`` with operational error code (orchestration outcome)."""
    fs = failure_stage.value if failure_stage else None
    _mark_job_failed(session, job, message, failure_stage=fs, failure_code=fs)
    ws.status = WorkspaceStatus.ERROR.value
    _workspace_set_error(ws, _ERROR_CODE_JOB, message)
    _touch_workspace(session, ws)
    if ws.workspace_id is not None:
        _clear_runtime_capacity_reservation(session, ws.workspace_id)
    record_audit(
        session,
        action=AuditAction.WORKSPACE_JOB_FAILED.value,
        resource_type="workspace",
        resource_id=ws.workspace_id,
        actor_user_id=job.requested_by_user_id,
        actor_type=AuditActorType.SYSTEM.value,
        outcome=AuditOutcome.FAILURE.value,
        workspace_id=ws.workspace_id,
        job_id=job.workspace_job_id,
        correlation_id=job.correlation_id,
        reason=message[:4096],
        metadata={"job_type": job.job_type, "failure_stage": fs},
    )
    record_usage(
        session,
        workspace_id=int(ws.workspace_id),
        owner_user_id=int(ws.owner_user_id),
        event_type=UsageEventType.WORKSPACE_JOB_FAILED.value,
        job_id=job.workspace_job_id,
        correlation_id=job.correlation_id,
    )


def _resolve_orchestrator_result_failure(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
    *,
    message: str,
    stage: FailureStage,
    retryable: bool,
) -> None:
    """Bounded retry for orchestrator *result* failures (success=False), else terminal workspace ERROR."""
    if retryable and try_schedule_workspace_job_retry(
        session,
        job,
        message=message,
        stage=stage,
        failure_code=stage.value,
        truncate_message=_truncate,
        now=_now(),
    ):
        _touch_workspace(session, ws)
        return
    _finalize_job_failed_workspace_error(session, ws, job, message=message, failure_stage=stage)


def _sync_workspace_execution_node_id(session: Session, ws: Workspace, node_id: str | None) -> None:
    """Align ``Workspace.execution_node_id`` with the registry row for ``node_key`` (Phase 1)."""
    key = (node_id or "").strip()
    if not key:
        return
    row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if row is None or row.id is None:
        return
    nid = int(row.id)
    if ws.execution_node_id != nid:
        ws.execution_node_id = nid
        session.add(ws)


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
    gateway_route_target: str | None = None,
    reserved_cpu: float = DEFAULT_WORKSPACE_REQUEST_CPU,
    reserved_memory_mb: int = DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
    reserved_disk_mb: int = DEFAULT_WORKSPACE_REQUEST_DISK_MB,
) -> None:
    """
    Shared success path for CREATE/START, RESTART, and UPDATE (restart path): persist runtime,
    mark job succeeded, set workspace ``RUNNING`` and clear last error fields.
    """
    wid = ws.workspace_id
    assert wid is not None
    gateway_route_target = _remote_gateway_route_target_for_node(
        session,
        workspace_id=wid,
        node_key=node_id,
        execution_node_id=ws.execution_node_id,
        gateway_route_target=gateway_route_target,
        internal_endpoint=internal_endpoint,
    )
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
        gateway_route_target=gateway_route_target,
        reserved_cpu=reserved_cpu,
        reserved_memory_mb=reserved_memory_mb,
        reserved_disk_mb=reserved_disk_mb,
    )
    _sync_workspace_execution_node_id(session, ws, node_id)
    _mark_job_succeeded(session, job)
    ws.status = WorkspaceStatus.RUNNING.value
    _workspace_clear_errors(ws)
    ws.endpoint_ref = gateway_route_target or internal_endpoint or ws.endpoint_ref
    ws.last_started = _now()
    _touch_workspace(session, ws)
    revoke_all_workspace_sessions(
        session,
        wid,
        reason="worker.runtime_running",
        correlation_id=job.correlation_id,
    )
    _gateway_try_register_running(session, ws)
    record_audit(
        session,
        action=AuditAction.WORKSPACE_JOB_SUCCEEDED.value,
        resource_type="workspace",
        resource_id=wid,
        actor_user_id=job.requested_by_user_id,
        actor_type=AuditActorType.SYSTEM.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=wid,
        job_id=job.workspace_job_id,
        node_id=node_id,
        correlation_id=job.correlation_id,
        metadata={"job_type": job.job_type, "new_status": WorkspaceStatus.RUNNING.value},
    )
    record_usage(
        session,
        workspace_id=wid,
        owner_user_id=int(ws.owner_user_id),
        event_type=UsageEventType.WORKSPACE_STARTED.value,
        node_id=node_id,
        job_id=job.workspace_job_id,
        correlation_id=job.correlation_id,
    )


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
            gateway_route_target=result.gateway_route_target,
        )
        return

    _sync_runtime_after_failed_bringup(session, wid, result)
    extra_rb = _format_issues(result.rollback_issues)
    base_msg = _format_issues(result.issues) or "Bring-up completed without success"
    msg = f"{base_msg}; rollback_failed: {extra_rb}" if (result.rollback_succeeded is False and extra_rb) else base_msg
    _resolve_orchestrator_result_failure(
        session,
        ws,
        job,
        message=msg,
        stage=FailureStage.CONTAINER,
        retryable=lifecycle_result_failure_retryable(job.job_type),
    )


def _finalize_stop_result(session: Session, ws: Workspace, job: WorkspaceJob, result: WorkspaceStopResult) -> None:
    wid = ws.workspace_id
    assert wid is not None
    if result.success:
        gw_meta = _gateway_route_telemetry_before_runtime_clear(session, ws, wid)
        _apply_runtime_stop(session, wid, result)
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.STOPPED.value
        _workspace_clear_errors(ws)
        ws.last_stopped = _now()
        _touch_workspace(session, ws)
        revoke_all_workspace_sessions(
            session,
            wid,
            reason="worker.stop",
            correlation_id=job.correlation_id,
        )
        _gateway_try_deregister(
            wid,
            public_host=gw_meta.get("public_host"),
            node_key=gw_meta.get("node_key"),
            gateway_upstream_target=gw_meta.get("gateway_upstream_target"),
        )
        record_audit(
            session,
            action=AuditAction.WORKSPACE_JOB_SUCCEEDED.value,
            resource_type="workspace",
            resource_id=wid,
            actor_user_id=job.requested_by_user_id,
            actor_type=AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.SUCCESS.value,
            workspace_id=wid,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
            metadata={"job_type": job.job_type, "new_status": WorkspaceStatus.STOPPED.value},
        )
        runtime_secs = 0
        if ws.last_started is not None:
            runtime_secs = max(0, int((_now() - ws.last_started).total_seconds()))
        record_usage(
            session,
            workspace_id=wid,
            owner_user_id=int(ws.owner_user_id),
            event_type=UsageEventType.WORKSPACE_STOPPED.value,
            quantity=runtime_secs,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
        )
        return

    msg = _format_issues(result.issues) or "Stop completed without success"
    ensure_durable_cleanup_task(
        session,
        workspace_id=wid,
        scope=CLEANUP_SCOPE_STOP_INCOMPLETE,
        detail=list(result.issues or []),
    )
    _resolve_orchestrator_result_failure(
        session,
        ws,
        job,
        message=msg,
        stage=FailureStage.CONTAINER,
        retryable=False,
    )


def _finalize_delete_result(session: Session, ws: Workspace, job: WorkspaceJob, result: WorkspaceDeleteResult) -> None:
    wid = ws.workspace_id
    assert wid is not None
    if result.success:
        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.DELETED.value
        _workspace_clear_errors(ws)
        gw_meta = _gateway_route_telemetry_before_runtime_clear(session, ws, wid)
        _clear_runtime_after_delete(session, wid)
        _touch_workspace(session, ws)
        revoke_all_workspace_sessions(
            session,
            wid,
            reason="worker.delete",
            correlation_id=job.correlation_id,
        )
        _gateway_try_deregister(
            wid,
            public_host=gw_meta.get("public_host"),
            node_key=gw_meta.get("node_key"),
            gateway_upstream_target=gw_meta.get("gateway_upstream_target"),
        )
        record_audit(
            session,
            action=AuditAction.WORKSPACE_JOB_SUCCEEDED.value,
            resource_type="workspace",
            resource_id=wid,
            actor_user_id=job.requested_by_user_id,
            actor_type=AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.SUCCESS.value,
            workspace_id=wid,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
            metadata={"job_type": job.job_type, "new_status": WorkspaceStatus.DELETED.value},
        )
        record_usage(
            session,
            workspace_id=wid,
            owner_user_id=int(ws.owner_user_id),
            event_type=UsageEventType.WORKSPACE_DELETED.value,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
        )
        return

    msg = _format_issues(result.issues) or "Delete completed without success"
    _resolve_orchestrator_result_failure(
        session,
        ws,
        job,
        message=msg,
        stage=FailureStage.CONTAINER,
        retryable=False,
    )


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
            gateway_route_target=result.gateway_route_target,
        )
        return

    msg = _format_issues(result.issues) or "Restart completed without success"
    _resolve_orchestrator_result_failure(
        session,
        ws,
        job,
        message=msg,
        stage=FailureStage.CONTAINER,
        retryable=lifecycle_result_failure_retryable(job.job_type),
    )


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
            gateway_route_target=result.gateway_route_target,
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
        rt.gateway_route_target = result.gateway_route_target
        rt.config_version = cfg_v
        rt.reserved_cpu = 0.0
        rt.reserved_memory_mb = 0
        rt.reserved_disk_mb = 0
        rt.health_status = WorkspaceRuntimeHealthStatus.UNKNOWN.value
        rt.last_heartbeat_at = None
        rt.updated_at = ts
        session.add(rt)

        _mark_job_succeeded(session, job)
        ws.status = WorkspaceStatus.STOPPED.value
        _workspace_clear_errors(ws)
        ws.status_reason = _truncate(msg, 1024)
        _touch_workspace(session, ws)
        revoke_all_workspace_sessions(
            session,
            wid,
            reason="worker.update_noop_stopped",
            correlation_id=job.correlation_id,
        )
        return

    _resolve_orchestrator_result_failure(
        session,
        ws,
        job,
        message=msg,
        stage=FailureStage.CONTAINER,
        retryable=lifecycle_result_failure_retryable(job.job_type),
    )


def _execute_snapshot_create_job(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
    """Materialize snapshot archive via orchestrator export; snapshot row is pre-created by API.

    On export failure, best-effort removal of a partial archive avoids orphaned blobs under the
    storage root.
    """
    wid = ws.workspace_id
    assert wid is not None
    wid_str = str(wid)
    sid = job.workspace_snapshot_id
    if sid is None:
        _mark_job_failed(
            session,
            job,
            "SNAPSHOT_CREATE job missing workspace_snapshot_id",
            failure_stage=FailureStage.UNKNOWN.value,
            failure_code="SNAPSHOT_JOB_INVALID",
        )
        _touch_workspace(session, ws)
        return

    snap = session.get(WorkspaceSnapshot, sid)
    if snap is None or int(snap.workspace_id) != int(wid):
        _mark_job_failed(
            session,
            job,
            "Snapshot row missing or workspace mismatch",
            failure_stage=FailureStage.UNKNOWN.value,
            failure_code="SNAPSHOT_ROW_INVALID",
        )
        if snap is not None:
            snap.status = WorkspaceSnapshotStatus.FAILED.value
        _touch_workspace(session, ws)
        return

    storage = get_snapshot_storage_provider()
    archive_path = storage.archive_path(workspace_id=wid, snapshot_id=sid)
    rt = session.exec(select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)).first()
    snapshot_cid: str | None = None
    if ws.status == WorkspaceStatus.RUNNING.value and rt is not None:
        st = (rt.container_state or "").strip().lower()
        if st == "running" and (rt.container_id or "").strip():
            snapshot_cid = str(rt.container_id).strip()

    log_event(
        logger,
        LogEvent.ORCHESTRATOR_SNAPSHOT_EXPORT_STARTED,
        correlation_id=job.correlation_id,
        workspace_id=wid,
        workspace_snapshot_id=sid,
        execution_node_id=ws.execution_node_id,
        workspace_runtime_node_id=(rt.node_id or "").strip() if rt is not None else None,
        workspace_runtime_topology_id=int(rt.topology_id)
        if rt is not None and rt.topology_id is not None
        else None,
        snapshot_container_id=snapshot_cid,
    )

    res = orchestrator.export_workspace_filesystem_snapshot(
        workspace_id=wid_str,
        project_storage_key=ws.project_storage_key,
        archive_path=archive_path,
        container_id=snapshot_cid,
    )

    if res.success:
        # For object-storage providers (e.g. S3), upload the local staging archive to remote
        # storage after the orchestrator writes it.  Local providers are a no-op here.
        if hasattr(storage, "upload_archive"):
            try:
                upload_kw: dict[str, object] = {}
                src_nk: str | None = None
                if rt is not None:
                    nk = (rt.node_id or "").strip() or None
                    if nk:
                        upload_kw["source_node_key"] = nk
                        src_nk = nk
                src_eid: int | None = None
                if ws.execution_node_id is not None:
                    src_eid = int(ws.execution_node_id)
                    upload_kw["source_execution_node_id"] = src_eid
                log_event(
                    logger,
                    LogEvent.SNAPSHOT_STORAGE_UPLOAD_STARTED,
                    correlation_id=job.correlation_id,
                    workspace_id=wid,
                    workspace_snapshot_id=sid,
                    source_node_key=src_nk,
                    source_execution_node_id=src_eid,
                )
                storage.upload_archive(workspace_id=wid, snapshot_id=sid, **upload_kw)
                log_event(
                    logger,
                    LogEvent.SNAPSHOT_STORAGE_UPLOAD_SUCCEEDED,
                    correlation_id=job.correlation_id,
                    workspace_id=wid,
                    workspace_snapshot_id=sid,
                    source_node_key=src_nk,
                    source_execution_node_id=src_eid,
                )
            except Exception as upload_exc:
                log_event(
                    logger,
                    LogEvent.SNAPSHOT_STORAGE_UPLOAD_FAILED,
                    correlation_id=job.correlation_id,
                    workspace_id=wid,
                    workspace_snapshot_id=sid,
                )
                snap.status = WorkspaceSnapshotStatus.FAILED.value
                session.add(snap)
                _mark_job_failed(
                    session,
                    job,
                    f"snapshot:create:upload_failed:{upload_exc}",
                    failure_stage=FailureStage.STORAGE.value,
                    failure_code="SNAPSHOT_CREATE_UPLOAD_FAILED",
                )
                _touch_workspace(session, ws)
                return

        snap.storage_uri = storage.storage_uri(workspace_id=wid, snapshot_id=sid)
        snap.size_bytes = int(res.size_bytes or 0)
        snap.status = WorkspaceSnapshotStatus.AVAILABLE.value
        session.add(snap)
        _mark_job_succeeded(session, job)
        _touch_workspace(session, ws)
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.SNAPSHOT_CREATED,
            status=ws.status,
            message="Workspace snapshot materialized",
            payload={
                "job_id": job.workspace_job_id,
                "workspace_snapshot_id": sid,
                "size_bytes": snap.size_bytes,
                "storage_uri": snap.storage_uri,
            },
        )
        log_event(
            logger,
            LogEvent.WORKSPACE_SNAPSHOT_COMPLETED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=job.workspace_job_id,
            workspace_snapshot_id=sid,
            size_bytes=snap.size_bytes,
        )
        record_audit(
            session,
            action=AuditAction.WORKSPACE_SNAPSHOT_CREATED.value,
            resource_type="workspace_snapshot",
            resource_id=sid,
            actor_user_id=job.requested_by_user_id,
            actor_type=AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.SUCCESS.value,
            workspace_id=wid,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
            metadata={"size_bytes": snap.size_bytes},
        )
        record_usage(
            session,
            workspace_id=wid,
            owner_user_id=int(ws.owner_user_id),
            event_type=UsageEventType.SNAPSHOT_CREATED.value,
            quantity=int(snap.size_bytes or 0),
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
        )
        return

    msg = "; ".join(res.issues or ["snapshot:create:failed"])
    snap.status = WorkspaceSnapshotStatus.FAILED.value
    session.add(snap)
    try:
        storage.delete_archive(workspace_id=wid, snapshot_id=sid)
    except Exception:
        logger.warning(
            "snapshot_create_cleanup_archive_failed",
            extra={"workspace_id": wid, "workspace_snapshot_id": sid},
            exc_info=True,
        )
    _mark_job_failed(
        session,
        job,
        msg,
        failure_stage=FailureStage.STORAGE.value,
        failure_code="SNAPSHOT_CREATE_FAILED",
    )
    _touch_workspace(session, ws)
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.SNAPSHOT_FAILED,
        status=ws.status,
        message=msg,
        payload={"job_id": job.workspace_job_id, "workspace_snapshot_id": sid},
    )
    log_event(
        logger,
        LogEvent.WORKSPACE_SNAPSHOT_FAILED,
        correlation_id=job.correlation_id,
        workspace_id=wid,
        workspace_snapshot_id=sid,
        phase="create",
    )
    record_audit(
        session,
        action=AuditAction.WORKSPACE_SNAPSHOT_CREATE_FAILED.value,
        resource_type="workspace_snapshot",
        resource_id=sid,
        actor_user_id=job.requested_by_user_id,
        actor_type=AuditActorType.SYSTEM.value,
        outcome=AuditOutcome.FAILURE.value,
        workspace_id=wid,
        job_id=job.workspace_job_id,
        correlation_id=job.correlation_id,
        reason=msg,
    )


def _execute_repo_import_job(
    session: Session,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
    """Clone a repository into the workspace container (workspace must be RUNNING).

    Reads ``repo_id`` from ``job.config_json``, resolves the ``WorkspaceRepository`` row,
    fetches the decrypted provider token (if any), and runs ``git clone`` inside the
    container via the node execution bundle.  Updates ``WorkspaceRepository.clone_status``
    on success or failure.
    """
    from app.services.integration_service.git_executor import GitExecutionError, run_git_in_container  # noqa: PLC0415
    from app.services.integration_service.models import WorkspaceRepository  # noqa: PLC0415
    from app.services.integration_service.api.routers.provider_tokens import resolve_provider_token  # noqa: PLC0415
    from app.services.node_execution_service.factory import resolve_node_execution_bundle  # noqa: PLC0415

    wid = int(ws.workspace_id)
    # Find the workspace's repo in pending/cloning state.
    repo = session.exec(
        select(WorkspaceRepository).where(
            WorkspaceRepository.workspace_id == wid,
            WorkspaceRepository.clone_status.in_(["pending", "cloning"]),
        ).order_by(WorkspaceRepository.repo_id.desc())
    ).first()
    if repo is None:
        _mark_job_failed(session, job, f"No pending WorkspaceRepository for workspace {wid}", failure_stage=FailureStage.ORCHESTRATION.value, failure_code="REPO_IMPORT_NOT_FOUND")
        return

    repo.clone_status = "cloning"
    repo.updated_at = datetime.now(timezone.utc)
    session.add(repo)
    session.flush()

    # Resolve execution bundle for the running workspace container.
    runtime = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == wid)
    ).first()
    if runtime is None or not runtime.container_id:
        repo.clone_status = "failed"
        repo.error_msg = "Workspace runtime container not available"
        repo.updated_at = datetime.now(timezone.utc)
        session.add(repo)
        _mark_job_failed(session, job, repo.error_msg, failure_stage=FailureStage.ORCHESTRATION.value, failure_code="REPO_IMPORT_NO_RUNTIME")
        return

    provider_token: str | None = None
    if repo.provider and repo.owner_user_id:
        provider_token = resolve_provider_token(session, int(repo.owner_user_id), repo.provider)

    try:
        bundle = resolve_node_execution_bundle(session, runtime.node_id)
        git_result = run_git_in_container(
            bundle,
            runtime.container_id,
            ["clone", "--branch", repo.branch, repo.repo_url, repo.clone_dir],
            workdir="/",
            provider_token=provider_token,
            timeout_seconds=300,
        )
    except (GitExecutionError, Exception) as exc:
        msg = str(exc)[:512]
        repo.clone_status = "failed"
        repo.error_msg = msg
        repo.updated_at = datetime.now(timezone.utc)
        session.add(repo)
        _mark_job_failed(session, job, msg, failure_stage=FailureStage.ORCHESTRATION.value, failure_code="REPO_IMPORT_EXEC_ERROR")
        return

    if not git_result.success:
        repo.clone_status = "failed"
        repo.error_msg = git_result.output[:512]
        repo.updated_at = datetime.now(timezone.utc)
        session.add(repo)
        _mark_job_failed(session, job, git_result.output[:256], failure_stage=FailureStage.ORCHESTRATION.value, failure_code="REPO_IMPORT_GIT_ERROR")
        return

    now = datetime.now(timezone.utc)
    repo.clone_status = "cloned"
    repo.last_synced_at = now
    repo.error_msg = None
    repo.updated_at = now
    session.add(repo)
    _mark_job_succeeded(session, job)
    log_event(logger, LogEvent.WORKSPACE_JOB_SUCCEEDED, workspace_id=wid, workspace_job_id=job.workspace_job_id, job_type=job.job_type)


def _execute_snapshot_restore_job(
    session: Session,
    orchestrator: OrchestratorService,
    ws: Workspace,
    job: WorkspaceJob,
) -> None:
    """Extract snapshot archive into workspace project dir (workspace must be STOPPED).

    TODO: For stronger safety, extract to a temp directory then atomic rename/swap (avoids torn
    trees if import fails mid-way); V1 uses in-place extract after path validation in orchestrator.
    """
    wid = ws.workspace_id
    assert wid is not None
    wid_str = str(wid)
    sid = job.workspace_snapshot_id
    if sid is None:
        _mark_job_failed(
            session,
            job,
            "SNAPSHOT_RESTORE job missing workspace_snapshot_id",
            failure_stage=FailureStage.UNKNOWN.value,
            failure_code="SNAPSHOT_JOB_INVALID",
        )
        _touch_workspace(session, ws)
        return

    snap = session.get(WorkspaceSnapshot, sid)
    if snap is None or int(snap.workspace_id) != int(wid):
        _mark_job_failed(
            session,
            job,
            "Snapshot row missing or workspace mismatch",
            failure_stage=FailureStage.UNKNOWN.value,
            failure_code="SNAPSHOT_ROW_INVALID",
        )
        _touch_workspace(session, ws)
        return

    storage = get_snapshot_storage_provider()
    archive_path = storage.archive_path(workspace_id=wid, snapshot_id=sid)

    # For object-storage providers (e.g. S3), download the archive to the local staging path
    # before the orchestrator reads it.  Local providers are a no-op here.
    if hasattr(storage, "download_archive"):
        try:
            log_event(
                logger,
                LogEvent.SNAPSHOT_STORAGE_DOWNLOAD_STARTED,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_snapshot_id=sid,
            )
            storage.download_archive(workspace_id=wid, snapshot_id=sid)
            log_event(
                logger,
                LogEvent.SNAPSHOT_STORAGE_DOWNLOAD_SUCCEEDED,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_snapshot_id=sid,
            )
        except Exception as dl_exc:
            log_event(
                logger,
                LogEvent.SNAPSHOT_STORAGE_DOWNLOAD_FAILED,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_snapshot_id=sid,
            )
            snap.status = WorkspaceSnapshotStatus.AVAILABLE.value
            session.add(snap)
            _mark_job_failed(
                session,
                job,
                f"snapshot:restore:download_failed:{dl_exc}",
                failure_stage=FailureStage.STORAGE.value,
                failure_code="SNAPSHOT_RESTORE_DOWNLOAD_FAILED",
            )
            _touch_workspace(session, ws)
            return

    if not storage.has_nonempty_archive(workspace_id=wid, snapshot_id=sid):
        msg = f"snapshot:restore:archive_missing_or_empty:{archive_path}"
        snap.status = WorkspaceSnapshotStatus.AVAILABLE.value
        session.add(snap)
        _mark_job_failed(
            session,
            job,
            msg,
            failure_stage=FailureStage.STORAGE.value,
            failure_code="SNAPSHOT_RESTORE_MISSING_ARCHIVE",
        )
        _touch_workspace(session, ws)
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.SNAPSHOT_FAILED,
            status=ws.status,
            message=msg,
            payload={"job_id": job.workspace_job_id, "workspace_snapshot_id": sid},
        )
        log_event(
            logger,
            LogEvent.WORKSPACE_SNAPSHOT_FAILED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_snapshot_id=sid,
            phase="restore",
        )
        return

    res = orchestrator.import_workspace_filesystem_snapshot(
        workspace_id=wid_str,
        project_storage_key=ws.project_storage_key,
        archive_path=archive_path,
    )

    if res.success:
        snap.status = WorkspaceSnapshotStatus.AVAILABLE.value
        session.add(snap)
        _mark_job_succeeded(session, job)
        _touch_workspace(session, ws)
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.SNAPSHOT_RESTORED,
            status=ws.status,
            message="Workspace files restored from snapshot",
            payload={
                "job_id": job.workspace_job_id,
                "workspace_snapshot_id": sid,
                "size_bytes": res.size_bytes,
            },
        )
        log_event(
            logger,
            LogEvent.WORKSPACE_SNAPSHOT_RESTORED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_snapshot_id=sid,
        )
        record_audit(
            session,
            action=AuditAction.WORKSPACE_SNAPSHOT_RESTORED.value,
            resource_type="workspace_snapshot",
            resource_id=sid,
            actor_user_id=job.requested_by_user_id,
            actor_type=AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.SUCCESS.value,
            workspace_id=wid,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
        )
        record_usage(
            session,
            workspace_id=wid,
            owner_user_id=int(ws.owner_user_id),
            event_type=UsageEventType.SNAPSHOT_RESTORED.value,
            job_id=job.workspace_job_id,
            correlation_id=job.correlation_id,
        )
        return

    msg = "; ".join(res.issues or ["snapshot:restore:failed"])
    snap.status = WorkspaceSnapshotStatus.AVAILABLE.value
    session.add(snap)
    _mark_job_failed(
        session,
        job,
        msg,
        failure_stage=FailureStage.STORAGE.value,
        failure_code="SNAPSHOT_RESTORE_FAILED",
    )
    _touch_workspace(session, ws)
    record_workspace_event(
        session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.SNAPSHOT_FAILED,
        status=ws.status,
        message=msg,
        payload={"job_id": job.workspace_job_id, "workspace_snapshot_id": sid},
    )
    log_event(
        logger,
        LogEvent.WORKSPACE_SNAPSHOT_FAILED,
        correlation_id=job.correlation_id,
        workspace_id=wid,
        workspace_snapshot_id=sid,
        phase="restore",
    )


def _execute_job_body(
    session: Session,
    orchestrator: OrchestratorService | None,
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

    if jt == WorkspaceJobType.REPO_IMPORT.value:
        _execute_repo_import_job(session, ws, job)
        return

    assert orchestrator is not None

    if jt in (WorkspaceJobType.CREATE.value, WorkspaceJobType.START.value):
        # Load workspace config for resource limits and feature flags.
        _cfg_row = session.exec(
            select(WorkspaceConfig)
            .where(WorkspaceConfig.workspace_id == wid)
            .order_by(WorkspaceConfig.version.desc())
        ).first()
        _config_json: dict = (_cfg_row.config_json if _cfg_row else None) or {}
        _cpu_limit = _config_json.get("cpu_limit_cores")
        _mem_limit = _config_json.get("memory_limit_mib")
        _env = _config_json.get("env") or {}
        _secret_env = resolve_workspace_runtime_secret_env(session, workspace_id=wid)
        if isinstance(_env, dict):
            _env = {str(k): str(v) for k, v in _env.items()}
        else:
            _env = {}
        _env.update(_secret_env)
        _features = get_workspace_features(_config_json).model_dump()

        result = orchestrator.bring_up_workspace_runtime(
            workspace_id=wid_str,
            project_storage_key=ws.project_storage_key,
            requested_config_version=cfg_v,
            cpu_limit_cores=float(_cpu_limit) if _cpu_limit else None,
            memory_limit_mib=int(_mem_limit) if _mem_limit else None,
            env=_env,
            features=_features,
            launch_mode="new" if jt == WorkspaceJobType.CREATE.value else "resume",
        )
        _finalize_bringup_result(session, ws, job, result, config_version=cfg_v)
        return

    if jt == WorkspaceJobType.STOP.value:
        persisted_container_id = _get_persisted_container_id(session, wid)
        result = orchestrator.stop_workspace_runtime(
            workspace_id=wid_str,
            container_id=persisted_container_id,
            requested_by=requested_by,
        )
        _finalize_stop_result(session, ws, job, result)
        return

    if jt == WorkspaceJobType.DELETE.value:
        persisted_container_id = _get_persisted_container_id(session, wid)
        result = orchestrator.delete_workspace_runtime(
            workspace_id=wid_str,
            container_id=persisted_container_id,
            requested_by=requested_by,
        )
        _finalize_delete_result(session, ws, job, result)
        return

    if jt == WorkspaceJobType.RESTART.value:
        persisted_container_id = _get_persisted_container_id(session, wid)
        result = orchestrator.restart_workspace_runtime(
            workspace_id=wid_str,
            project_storage_key=ws.project_storage_key,
            container_id=persisted_container_id,
            requested_by=requested_by,
            requested_config_version=cfg_v,
        )
        _finalize_restart_result(session, ws, job, result, config_version=cfg_v)
        return

    if jt == WorkspaceJobType.UPDATE.value:
        persisted_container_id = _get_persisted_container_id(session, wid)
        result = orchestrator.update_workspace_runtime(
            workspace_id=wid_str,
            project_storage_key=ws.project_storage_key,
            container_id=persisted_container_id,
            requested_config_version=cfg_v,
            requested_by=requested_by,
        )
        _finalize_update_result(session, ws, job, result)
        return

    if jt == WorkspaceJobType.RECONCILE_RUNTIME.value:
        from app.services.reconcile_service.reconcile_runtime import execute_reconcile_runtime_job

        execute_reconcile_runtime_job(session, orchestrator, ws, job)
        return

    if jt == WorkspaceJobType.SNAPSHOT_CREATE.value:
        _execute_snapshot_create_job(session, orchestrator, ws, job)
        return

    if jt == WorkspaceJobType.SNAPSHOT_RESTORE.value:
        _execute_snapshot_restore_job(session, orchestrator, ws, job)
        return

    raise UnsupportedWorkspaceJobTypeError(f"Unsupported WorkspaceJob.type={jt!r}")


def _emit_job_outcome_event(session: Session, *, wid: int, ws: Workspace, job: WorkspaceJob) -> None:
    """Emit SSE/log events for terminal job outcomes, retry backoff, or success."""
    session.flush()
    session.refresh(job)
    session.refresh(ws)
    jid = job.workspace_job_id
    max_a = effective_max_attempts(job)
    base_payload: dict[str, object] = {
        "job_id": jid,
        "job_type": job.job_type,
        "workspace_status": ws.status,
        "attempt": job.attempt,
        "max_attempts": max_a,
    }
    if (
        job.status == WorkspaceJobStatus.QUEUED.value
        and job.next_attempt_after is not None
        and (job.error_msg or "").strip()
    ):
        retry_payload = {
            **base_payload,
            "failure_stage": job.failure_stage,
            "failure_code": job.failure_code,
            "error_msg": job.error_msg,
            "next_attempt_after": job.next_attempt_after.isoformat() if job.next_attempt_after else None,
            "retryable": True,
        }
        log_event(
            logger,
            LogEvent.WORKSPACE_JOB_RETRY_SCHEDULED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=jid,
            job_type=job.job_type,
            failure_stage=job.failure_stage,
            attempt=job.attempt,
            max_attempts=max_a,
        )
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_RETRY_SCHEDULED,
            status=ws.status,
            message="Workspace job retry scheduled after transient failure",
            payload=retry_payload,
        )
        if job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value:
            log_event(
                logger,
                LogEvent.RECONCILE_RETRY_SCHEDULED,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_job_id=jid,
                failure_stage=job.failure_stage,
            )
            record_workspace_event(
                session,
                workspace_id=wid,
                event_type=WorkspaceStreamEventType.RECONCILE_RETRY_SCHEDULED,
                status=ws.status,
                message="Reconcile job retry scheduled",
                payload=retry_payload,
            )
        return
    if job.status == WorkspaceJobStatus.SUCCEEDED.value:
        maybe_emit_workspace_lifecycle_notification(session, workspace=ws, job=job)
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
        maybe_emit_workspace_lifecycle_notification(session, workspace=ws, job=job)
        exhausted = int(job.attempt or 0) >= max_a
        terminal_payload = {
            **base_payload,
            "error_msg": job.error_msg,
            "failure_stage": job.failure_stage,
            "failure_code": job.failure_code,
            "terminal": True,
            "retry_exhausted": exhausted,
            "last_error_code": ws.last_error_code,
            "last_error_message": ws.last_error_message,
        }
        log_event(
            logger,
            LogEvent.WORKSPACE_JOB_FAILED,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=jid,
            job_type=job.job_type,
            error_msg=(job.error_msg or "")[:500] if job.error_msg else None,
            failure_stage=job.failure_stage,
        )
        log_event(
            logger,
            LogEvent.WORKSPACE_JOB_FAILED_TERMINAL,
            correlation_id=job.correlation_id,
            workspace_id=wid,
            workspace_job_id=jid,
            job_type=job.job_type,
            failure_stage=job.failure_stage,
            retry_exhausted=exhausted,
        )
        if exhausted:
            log_event(
                logger,
                LogEvent.WORKSPACE_JOB_RETRY_EXHAUSTED,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_job_id=jid,
                job_type=job.job_type,
            )
            record_workspace_event(
                session,
                workspace_id=wid,
                event_type=WorkspaceStreamEventType.JOB_RETRY_EXHAUSTED,
                status=ws.status,
                message="Workspace job retries exhausted",
                payload=terminal_payload,
            )
        record_workspace_event(
            session,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_FAILED,
            status=ws.status,
            message="Workspace job failed",
            payload=terminal_payload,
        )
        if job.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value:
            log_event(
                logger,
                LogEvent.RECONCILE_FAILED_TERMINAL,
                correlation_id=job.correlation_id,
                workspace_id=wid,
                workspace_job_id=jid,
                failure_stage=job.failure_stage,
            )
            record_workspace_event(
                session,
                workspace_id=wid,
                event_type=WorkspaceStreamEventType.RECONCILE_FAILED_TERMINAL,
                status=ws.status,
                message="Reconcile job failed (terminal)",
                payload=terminal_payload,
            )


def _process_claimed_running_job(
    session: Session,
    orchestrator: OrchestratorService | None,
    job: WorkspaceJob,
) -> None:
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
        _mark_job_failed(
            session,
            job,
            "Workspace row not found for job",
            failure_stage=FailureStage.UNKNOWN.value,
            failure_code="missing_workspace",
        )
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
        if isinstance(e, WorkspaceBringUpError) and e.rollback_attempted:
            _sync_runtime_after_bringup_exception(session, wid, e)
        logger.warning(
            "workspace_job_orchestrator_exception",
            extra={
                "workspace_id": wid,
                "workspace_job_id": jid,
                "job_type": jt,
                "error": str(e),
                "bringup_rollback_attempted": getattr(e, "rollback_attempted", False),
                "bringup_rollback_succeeded": getattr(e, "rollback_succeeded", None),
            },
        )
        msg = str(e)
        if isinstance(e, WorkspaceBringUpError) and e.rollback_succeeded is False:
            rb = _format_issues(e.rollback_issues)
            if rb:
                msg = f"{msg}; rollback_failed: {rb}"
        stage = (
            FailureStage.STORAGE
            if jt
            in (
                WorkspaceJobType.SNAPSHOT_CREATE.value,
                WorkspaceJobType.SNAPSHOT_RESTORE.value,
            )
            else FailureStage.CONTAINER
        )
        retry = orchestrator_exception_retryable(jt)
        if isinstance(e, (WorkspaceStopError, WorkspaceDeleteError)):
            retry = False
        scheduled = bool(
            retry
            and try_schedule_workspace_job_retry(
                session,
                job,
                message=msg,
                stage=stage,
                failure_code=_ERROR_CODE_ORCH,
                truncate_message=_truncate,
                now=_now(),
            ),
        )
        if scheduled:
            _touch_workspace(session, ws)
        else:
            _mark_job_failed(session, job, msg, failure_stage=stage.value, failure_code=_ERROR_CODE_ORCH)
            if jt == WorkspaceJobType.SNAPSHOT_CREATE.value and job.workspace_snapshot_id is not None:
                snap = session.get(WorkspaceSnapshot, job.workspace_snapshot_id)
                if snap is not None and snap.status == WorkspaceSnapshotStatus.CREATING.value:
                    snap.status = WorkspaceSnapshotStatus.FAILED.value
                    session.add(snap)
            elif jt == WorkspaceJobType.SNAPSHOT_RESTORE.value and job.workspace_snapshot_id is not None:
                snap = session.get(WorkspaceSnapshot, job.workspace_snapshot_id)
                if snap is not None:
                    snap.status = WorkspaceSnapshotStatus.AVAILABLE.value
                    session.add(snap)
            if jt not in (
                WorkspaceJobType.SNAPSHOT_CREATE.value,
                WorkspaceJobType.SNAPSHOT_RESTORE.value,
            ):
                ws.status = WorkspaceStatus.ERROR.value
                _workspace_set_error(ws, _ERROR_CODE_ORCH, msg)
                _touch_workspace(session, ws)
                _clear_runtime_capacity_reservation(session, wid)
            else:
                _touch_workspace(session, ws)
    except UnsupportedWorkspaceJobTypeError as e:
        logger.error(
            "workspace_job_unsupported_type",
            extra={"workspace_id": wid, "workspace_job_id": jid, "job_type": jt, "error": str(e)},
        )
        msg = str(e)
        _mark_job_failed(session, job, msg, failure_stage=FailureStage.UNKNOWN.value, failure_code=_ERROR_CODE_JOB)
        ws.status = WorkspaceStatus.ERROR.value
        _workspace_set_error(ws, _ERROR_CODE_JOB, msg)
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
            _mark_job_failed(
                session,
                job,
                "Workspace row not found for job",
                failure_stage=FailureStage.UNKNOWN.value,
                failure_code="missing_workspace",
            )
            return jid
        try:
            if job.job_type == WorkspaceJobType.REPO_IMPORT.value:
                from app.services.placement_service.orchestrator_binding import (
                    resolve_orchestrator_placement,
                )

                resolve_orchestrator_placement(session, ws, job)
                orchestrator = None
            else:
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
            _fail_job_from_placement(session, ws, job, e, placement_reason=pr)
            _emit_job_outcome_event(session, wid=wid, ws=ws, job=job)
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
            _fail_job_from_orchestrator_binding(session, ws, job, e)
            _emit_job_outcome_event(session, wid=wid, ws=ws, job=job)
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
        .where(queued_job_eligible_where(WorkspaceJob, _now()))
        .order_by(WorkspaceJob.created_at.asc())
        .limit(1)
    )
    return session.exec(stmt).first()


def reclaim_stuck_running_jobs(engine: "Engine") -> int:
    """Reclaim jobs that have been in ``RUNNING`` state beyond the stuck-timeout threshold.

    A job is considered stuck when ``started_at`` is older than
    ``workspace_job_stuck_timeout_seconds`` (default 300s). This handles worker crashes that
    left jobs permanently in ``RUNNING`` state.

    For each stuck job:
    - If retry attempts remain: re-schedule as ``QUEUED`` (backoff applied).
    - Otherwise: mark ``FAILED`` terminal and move workspace to ``ERROR`` if it is a lifecycle
      job type (not reconcile, not snapshot).

    Returns the number of jobs reclaimed.
    """
    from datetime import timedelta  # noqa: PLC0415

    settings = get_settings()
    timeout_seconds = int(getattr(settings, "workspace_job_stuck_timeout_seconds", 300))
    if timeout_seconds <= 0:
        return 0

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    reclaimed = 0

    sm = _worker_sessionmaker(engine)
    with sm() as session:
        stuck_jobs = session.exec(
            select(WorkspaceJob)
            .where(
                WorkspaceJob.status == WorkspaceJobStatus.RUNNING.value,
                WorkspaceJob.started_at <= cutoff,
            )
            .limit(20)  # Safety cap per tick.
        ).all()

        if not stuck_jobs:
            return 0

        for job in stuck_jobs:
            wid = int(job.workspace_id)
            ws = session.get(Workspace, wid)
            message = (
                f"stuck_running_reclaimed:job_id={job.workspace_job_id},"
                f"started_at={job.started_at}"
            )
            retried = try_schedule_workspace_job_retry(
                session,
                job,
                message=message,
                stage=FailureStage.UNKNOWN,
                failure_code="STUCK_RUNNING_RECLAIMED",
                truncate_message=_truncate,
            )
            if retried:
                log_event(
                    logger,
                    LogEvent.WORKER_STUCK_JOB_RETRY_SCHEDULED,
                    workspace_id=wid,
                    workspace_job_id=job.workspace_job_id,
                    job_type=job.job_type,
                )
                if ws is not None:
                    _touch_workspace(session, ws)
            else:
                # Retry exhausted — mark terminal.
                _mark_job_failed(
                    session,
                    job,
                    message,
                    failure_stage=FailureStage.UNKNOWN.value,
                    failure_code="STUCK_RUNNING_TERMINAL",
                )
                # For lifecycle jobs (not reconcile / snapshots), move workspace to ERROR.
                _lifecycle_job_types = {
                    WorkspaceJobType.CREATE.value,
                    WorkspaceJobType.START.value,
                    WorkspaceJobType.STOP.value,
                    WorkspaceJobType.RESTART.value,
                    WorkspaceJobType.UPDATE.value,
                    WorkspaceJobType.DELETE.value,
                }
                if ws is not None and job.job_type in _lifecycle_job_types:
                    ws.status = WorkspaceStatus.ERROR.value
                    _workspace_set_error(ws, "STUCK_RUNNING_TERMINAL", message)
                    _touch_workspace(session, ws)
                    _clear_runtime_capacity_reservation(session, wid)
                log_event(
                    logger,
                    LogEvent.WORKER_STUCK_JOB_TERMINAL,
                    workspace_id=wid,
                    workspace_job_id=job.workspace_job_id,
                    job_type=job.job_type,
                )
            reclaimed += 1

        if reclaimed:
            session.commit()
            log_event(
                logger,
                LogEvent.WORKER_STUCK_JOB_RECLAIMED,
                reclaimed_count=reclaimed,
                stuck_timeout_seconds=timeout_seconds,
            )

    return reclaimed


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

    cleanup_sess = sm()
    try:
        from app.services.cleanup_service import drain_pending_cleanup_tasks

        drain_pending_cleanup_tasks(cleanup_sess, limit_workspaces=max(8, max(1, limit)))
        cleanup_sess.commit()
    except Exception:
        cleanup_sess.rollback()
        logger.warning("cleanup_drain_tick_failed", exc_info=True)
    finally:
        cleanup_sess.close()

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
                _mark_job_failed(
                    work,
                    job,
                    "Workspace row not found for job",
                    failure_stage=FailureStage.UNKNOWN.value,
                    failure_code="missing_workspace",
                )
                work.commit()
                return WorkspaceJobWorkerTickResult(processed_count=1, last_job_id=jid)
            try:
                if job.job_type == WorkspaceJobType.REPO_IMPORT.value:
                    from app.services.placement_service.orchestrator_binding import (
                        resolve_orchestrator_placement,
                    )

                    resolve_orchestrator_placement(work, ws, job)
                    orch = None
                else:
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
                _fail_job_from_placement(work, ws, job, e, placement_reason=pr)
                _emit_job_outcome_event(work, wid=wid, ws=ws, job=job)
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
                _fail_job_from_orchestrator_binding(work, ws, job, e)
                _emit_job_outcome_event(work, wid=wid, ws=ws, job=job)
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
        try_emit_default_local_execution_node_heartbeat(bind)
