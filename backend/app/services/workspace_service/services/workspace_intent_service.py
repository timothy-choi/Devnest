"""Workspace control-plane intent: metadata rows and queued jobs (no orchestrator)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.observability.correlation import generate_correlation_id
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.metrics import record_job_queued

logger = logging.getLogger(__name__)

from app.services.workspace_service.api.schemas import (
    CreateWorkspaceRequest,
    WorkspaceDetailResponse,
    WorkspaceRuntimeSpecSchema,
    WorkspaceSummaryResponse,
)
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
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
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    record_workspace_event,
)
from app.services.workspace_service.services.workspace_session_service import (
    create_workspace_session,
    resolve_workspace_session_for_access,
)
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.service import record_usage
from app.services.placement_service.constants import (
    DEFAULT_WORKSPACE_REQUEST_CPU,
    DEFAULT_WORKSPACE_REQUEST_MEMORY_MB,
)
from app.services.policy_service.service import (
    evaluate_session_creation,
    evaluate_workspace_creation,
    evaluate_workspace_start,
)
from app.services.quota_service.service import (
    check_monthly_runtime_hours_quota,
    check_owner_compute_quota,
    check_running_workspace_quota,
    check_session_quota,
    check_workspace_quota,
)

# Stable mapping from job type to audit action name; defined at module level to avoid
# re-creating the dict on every intent request.
_INTENT_JOB_TYPE_TO_AUDIT_ACTION: dict[str, str] = {
    WorkspaceJobType.START.value: AuditAction.WORKSPACE_START_REQUESTED.value,
    WorkspaceJobType.STOP.value: AuditAction.WORKSPACE_STOP_REQUESTED.value,
    WorkspaceJobType.RESTART.value: AuditAction.WORKSPACE_RESTART_REQUESTED.value,
    WorkspaceJobType.DELETE.value: AuditAction.WORKSPACE_DELETE_REQUESTED.value,
    WorkspaceJobType.UPDATE.value: AuditAction.WORKSPACE_UPDATE_REQUESTED.value,
}


@dataclass(frozen=True, slots=True)
class CreateWorkspaceResult:
    workspace_id: int
    job_id: int
    config_version: int
    status: str


@dataclass(frozen=True, slots=True)
class WorkspaceIntentResult:
    """Normalized acceptance result for start/stop/restart/delete/update intents."""

    workspace_id: int
    accepted: bool
    status: str
    job_id: int
    job_type: str
    requested_config_version: int
    issues: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WorkspaceAccessResult:
    """Normalized access coordinates (read-only); ``success`` is True only when runtime is ready."""

    workspace_id: int
    success: bool
    status: str
    runtime_ready: bool
    endpoint_ref: str | None
    public_host: str | None
    internal_endpoint: str | None
    gateway_url: str | None
    issues: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WorkspaceAttachResult:
    """Attach when RUNNING + runtime placed; creates a :class:`~app.services.workspace_service.models.workspace_session.WorkspaceSession` row."""

    workspace_id: int
    accepted: bool
    status: str
    runtime_ready: bool
    active_sessions_count: int
    workspace_session_id: int
    session_token: str
    session_expires_at: datetime
    endpoint_ref: str | None
    public_host: str | None
    internal_endpoint: str | None
    gateway_url: str | None
    issues: tuple[str, ...] = field(default_factory=tuple)


_BUSY_STATUSES: frozenset[str] = frozenset(
    {
        WorkspaceStatus.CREATING.value,
        WorkspaceStatus.STARTING.value,
        WorkspaceStatus.STOPPING.value,
        WorkspaceStatus.RESTARTING.value,
        WorkspaceStatus.UPDATING.value,
        WorkspaceStatus.DELETING.value,
    }
)


def _latest_config_version(session: Session, workspace_id: int) -> int | None:
    stmt = (
        select(WorkspaceConfig)
        .where(WorkspaceConfig.workspace_id == workspace_id)
        .order_by(WorkspaceConfig.version.desc())
        .limit(1)
    )
    cfg = session.exec(stmt).first()
    return cfg.version if cfg is not None else None


def _get_owned_workspace(session: Session, workspace_id: int, owner_user_id: int) -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None or ws.owner_user_id != owner_user_id:
        raise WorkspaceNotFoundError("Workspace not found")
    # V1 attach/access are owner-only. ``is_private`` gates listing elsewhere; collaborators / shared
    # workspaces are deferred (TODO).
    return ws


def _require_not_busy(ws: Workspace) -> None:
    if ws.status in _BUSY_STATUSES:
        raise WorkspaceBusyError(f"Workspace is busy (status={ws.status})")


def _effective_correlation_id(passed: str | None) -> str:
    if passed and str(passed).strip():
        return str(passed).strip()[:64]
    return generate_correlation_id()


def _intent_config_version(session: Session, workspace_id: int) -> int:
    v = _latest_config_version(session, workspace_id)
    if v is None:
        raise WorkspaceInvalidStateError("Workspace has no configuration version")
    return v


def _get_workspace_runtime(session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    stmt = select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
    return session.exec(stmt).first()


def _runtime_ready_for_access(ws: Workspace, rt: WorkspaceRuntime | None) -> bool:
    if ws.status != WorkspaceStatus.RUNNING.value:
        return False
    if rt is None:
        return False
    return bool((rt.container_id or "").strip())


def _resolve_public_host_for_gateway_display(ws: Workspace, rt: WorkspaceRuntime | None) -> str | None:
    """Stored ``Workspace.public_host``, or default ``ws-{id}.{base_domain}`` when gateway is on and runtime is ready."""
    from app.libs.common.config import get_settings

    explicit = (ws.public_host or "").strip()
    if explicit:
        return explicit
    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        return None
    if ws.status != WorkspaceStatus.RUNNING.value:
        return None
    if rt is None or not (rt.container_id or "").strip():
        return None
    wid = ws.workspace_id
    if wid is None:
        return None
    dom = (settings.devnest_base_domain or "app.devnest.local").strip().strip(".")
    return f"ws-{wid}.{dom}"


def _gateway_public_host_for_url(host: str, scheme: str, port: int) -> str:
    """Append ``:port`` to ``gateway_url`` when Traefik is published on a non-default port."""
    if port <= 0:
        return host
    sch = (scheme or "http").strip().lower().rstrip(":")
    if sch == "http" and port == 80:
        return host
    if sch == "https" and port == 443:
        return host
    return f"{host}:{port}"


def _derive_gateway_url_v1(ws: Workspace, rt: WorkspaceRuntime | None) -> str | None:
    """Public URL clients would use via Traefik when ``DEVNEST_GATEWAY_ENABLED`` (DNS/TLS out of scope)."""
    from app.libs.common.config import get_settings

    settings = get_settings()
    if not settings.devnest_gateway_enabled:
        return None
    host = _resolve_public_host_for_gateway_display(ws, rt)
    if not host:
        return None
    scheme = (settings.devnest_gateway_public_scheme or "http").strip().rstrip(":")
    pub_port = int(settings.devnest_gateway_public_port or 0)
    host = _gateway_public_host_for_url(host, scheme, pub_port)
    return f"{scheme}://{host}/"


def _access_issues_for_runtime(rt: WorkspaceRuntime) -> tuple[str, ...]:
    if rt.health_status == WorkspaceRuntimeHealthStatus.HEALTHY.value:
        return ()
    return (f"access:runtime:health:{rt.health_status}",)


def _ensure_workspace_running_for_access(ws: Workspace) -> None:
    """Attach/access require a settled RUNNING control-plane state (use /start to provision, not attach)."""
    _require_not_busy(ws)
    if ws.status != WorkspaceStatus.RUNNING.value:
        raise WorkspaceInvalidStateError(
            "Access requires workspace status RUNNING; use POST /workspaces/start/{id} "
            f"or wait for provisioning (current={ws.status})",
        )


def get_workspace_access(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    workspace_session_token: str | None,
    correlation_id: str | None = None,
) -> WorkspaceAccessResult:
    """
    Return gateway/runtime coordinates when RUNNING, runtime is ready, and the workspace session token is valid.

    Updates ``last_seen_at`` on the session row (commit by caller's session scope).
    """
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _ensure_workspace_running_for_access(ws)
    rt = _get_workspace_runtime(session, workspace_id)
    if not _runtime_ready_for_access(ws, rt):
        raise WorkspaceInvalidStateError(
            "Workspace is RUNNING but runtime metadata is not ready for access yet; retry shortly.",
        )
    assert rt is not None
    resolve_workspace_session_for_access(
        session,
        workspace_id=workspace_id,
        user_id=owner_user_id,
        token_plain=workspace_session_token or "",
        correlation_id=correlation_id,
    )
    record_audit(
        session,
        action=AuditAction.WORKSPACE_ACCESS_GRANTED.value,
        resource_type="workspace",
        resource_id=workspace_id,
        actor_user_id=owner_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    issues = _access_issues_for_runtime(rt)
    return WorkspaceAccessResult(
        workspace_id=workspace_id,
        success=True,
        status=ws.status,
        runtime_ready=True,
        endpoint_ref=ws.endpoint_ref,
        public_host=_resolve_public_host_for_gateway_display(ws, rt),
        internal_endpoint=rt.internal_endpoint,
        gateway_url=_derive_gateway_url_v1(ws, rt),
        issues=issues,
    )


def request_attach_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    client_metadata: dict | None = None,
    correlation_id: str | None = None,
) -> WorkspaceAttachResult:
    """
    Grant a workspace session when RUNNING + runtime placed (same preconditions as :func:`get_workspace_access`).

    Does **not** start or provision the workspace; callers use ``POST /workspaces/start/{id}`` first.
    Returns a one-time opaque ``session_token`` for ``X-DevNest-Workspace-Session`` on GET access.
    """
    _ = requested_by_user_id
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _ensure_workspace_running_for_access(ws)
    rt = _get_workspace_runtime(session, workspace_id)
    if not _runtime_ready_for_access(ws, rt):
        raise WorkspaceInvalidStateError(
            "Workspace is RUNNING but runtime metadata is not ready for access yet; retry shortly.",
        )
    assert rt is not None
    issues = _access_issues_for_runtime(rt)

    # Quota + policy checks before creating the session
    _cid_attach = _effective_correlation_id(correlation_id)
    check_session_quota(
        session,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        current_session_count=int(ws.active_sessions_count or 0),
        correlation_id=_cid_attach,
    )
    evaluate_session_creation(
        session,
        owner_user_id=owner_user_id,
        workspace_id=workspace_id,
        correlation_id=_cid_attach,
    )

    plain_token, row = create_workspace_session(
        session,
        workspace_id=workspace_id,
        user_id=owner_user_id,
        client_metadata=client_metadata,
        correlation_id=correlation_id,
    )
    record_audit(
        session,
        action=AuditAction.WORKSPACE_ATTACH_GRANTED.value,
        resource_type="workspace_session",
        resource_id=row.workspace_session_id,
        actor_user_id=owner_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
        metadata={"session_id": row.workspace_session_id},
    )
    record_usage(
        session,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        event_type=UsageEventType.SESSION_CREATED.value,
        correlation_id=correlation_id,
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(ws)
    session.refresh(row)
    assert row.workspace_session_id is not None
    return WorkspaceAttachResult(
        workspace_id=workspace_id,
        accepted=True,
        status=ws.status,
        runtime_ready=True,
        active_sessions_count=ws.active_sessions_count,
        workspace_session_id=row.workspace_session_id,
        session_token=plain_token,
        session_expires_at=row.expires_at,
        endpoint_ref=ws.endpoint_ref,
        public_host=_resolve_public_host_for_gateway_display(ws, rt),
        internal_endpoint=rt.internal_endpoint,
        gateway_url=_derive_gateway_url_v1(ws, rt),
        issues=issues,
    )


_START_JOB_TYPES = frozenset({WorkspaceJobType.START.value, WorkspaceJobType.RESTART.value})


def _persist_intent(
    session: Session,
    ws: Workspace,
    *,
    new_status: str,
    job_type: str,
    requested_by_user_id: int,
    requested_config_version: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    # Quota + policy checks before any state mutation
    cid_pre = _effective_correlation_id(correlation_id)
    if job_type in _START_JOB_TYPES:
        check_running_workspace_quota(
            session,
            owner_user_id=int(ws.owner_user_id),
            workspace_id=ws.workspace_id,
            correlation_id=cid_pre,
        )
        check_monthly_runtime_hours_quota(
            session,
            owner_user_id=int(ws.owner_user_id),
            correlation_id=cid_pre,
        )
        cfg_row = session.exec(
            select(WorkspaceConfig)
            .where(WorkspaceConfig.workspace_id == ws.workspace_id)
            .order_by(WorkspaceConfig.version.desc()),
        ).first()
        cfg_json: dict = (cfg_row.config_json if cfg_row else None) or {}
        p_cpu = float(cfg_json.get("cpu_limit_cores") or DEFAULT_WORKSPACE_REQUEST_CPU)
        p_mem = int(cfg_json.get("memory_limit_mib") or DEFAULT_WORKSPACE_REQUEST_MEMORY_MB)
        assert ws.workspace_id is not None
        check_owner_compute_quota(
            session,
            owner_user_id=int(ws.owner_user_id),
            proposed_cpu=p_cpu,
            proposed_memory_mb=p_mem,
            ignore_workspace_id=int(ws.workspace_id),
            correlation_id=cid_pre,
        )
        evaluate_workspace_start(
            session,
            owner_user_id=int(ws.owner_user_id),
            workspace_id=int(ws.workspace_id),
            correlation_id=cid_pre,
        )

    now = datetime.now(timezone.utc)
    ws.status = new_status
    ws.updated_at = now
    session.add(ws)

    cid = _effective_correlation_id(correlation_id)
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=job_type,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=requested_config_version,
        attempt=0,
        correlation_id=cid,
    )
    session.add(job)
    session.flush()

    record_job_queued(job_type=job_type)
    assert job.workspace_job_id is not None
    assert ws.workspace_id is not None
    log_event(
        logger,
        LogEvent.WORKSPACE_JOB_QUEUED,
        correlation_id=cid,
        workspace_id=ws.workspace_id,
        workspace_job_id=job.workspace_job_id,
        job_type=job_type,
    )

    record_workspace_event(
        session,
        workspace_id=ws.workspace_id,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=new_status,
        message="Intent accepted; job queued",
        payload={
            "job_id": job.workspace_job_id,
            "job_type": job_type,
            "requested_config_version": requested_config_version,
        },
    )

    audit_action = _INTENT_JOB_TYPE_TO_AUDIT_ACTION.get(job_type, f"workspace.{job_type.lower()}.requested")
    record_audit(
        session,
        action=audit_action,
        resource_type="workspace",
        resource_id=ws.workspace_id,
        actor_user_id=requested_by_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=ws.workspace_id,
        job_id=job.workspace_job_id,
        correlation_id=cid,
        metadata={"job_type": job_type, "new_status": new_status},
    )

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    assert ws.workspace_id is not None
    session.refresh(ws)
    session.refresh(job)
    assert job.workspace_job_id is not None

    return WorkspaceIntentResult(
        workspace_id=ws.workspace_id,
        accepted=True,
        status=ws.status,
        job_id=job.workspace_job_id,
        job_type=job_type,
        requested_config_version=requested_config_version,
        issues=(),
    )


def request_start_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _require_not_busy(ws)
    if ws.status not in (WorkspaceStatus.STOPPED.value, WorkspaceStatus.ERROR.value):
        raise WorkspaceInvalidStateError(
            f"Start is only allowed when stopped or error (current={ws.status})"
        )
    cfg_v = _intent_config_version(session, workspace_id)
    return _persist_intent(
        session,
        ws,
        new_status=WorkspaceStatus.STARTING.value,
        job_type=WorkspaceJobType.START.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=cfg_v,
        correlation_id=correlation_id,
    )


def request_stop_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _require_not_busy(ws)
    if ws.status != WorkspaceStatus.RUNNING.value:
        raise WorkspaceInvalidStateError(
            f"Stop is only allowed when running (current={ws.status})"
        )
    cfg_v = _intent_config_version(session, workspace_id)
    return _persist_intent(
        session,
        ws,
        new_status=WorkspaceStatus.STOPPING.value,
        job_type=WorkspaceJobType.STOP.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=cfg_v,
        correlation_id=correlation_id,
    )


def request_restart_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _require_not_busy(ws)
    if ws.status not in (WorkspaceStatus.RUNNING.value, WorkspaceStatus.STOPPED.value):
        raise WorkspaceInvalidStateError(
            f"Restart is only allowed when running or stopped (current={ws.status})"
        )
    cfg_v = _intent_config_version(session, workspace_id)
    return _persist_intent(
        session,
        ws,
        new_status=WorkspaceStatus.RESTARTING.value,
        job_type=WorkspaceJobType.RESTART.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=cfg_v,
        correlation_id=correlation_id,
    )


def request_delete_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _require_not_busy(ws)
    if ws.status not in (
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
    ):
        raise WorkspaceInvalidStateError(
            f"Delete is only allowed when running, stopped, or error (current={ws.status})"
        )
    cfg_v = _intent_config_version(session, workspace_id)
    return _persist_intent(
        session,
        ws,
        new_status=WorkspaceStatus.DELETING.value,
        job_type=WorkspaceJobType.DELETE.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=cfg_v,
        correlation_id=correlation_id,
    )


def request_update_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    runtime: WorkspaceRuntimeSpecSchema,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    """
    Stage the next config version (``latest + 1``) and enqueue an UPDATE job.

    The target ``requested_config_version`` in the result is the new row's version (not passed in
    by the client) so callers cannot desync from the authoritative sequence in the database.
    """
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _require_not_busy(ws)
    if ws.status not in (
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
    ):
        raise WorkspaceInvalidStateError(
            f"Update is only allowed when running, stopped, or error (current={ws.status})"
        )
    latest = _latest_config_version(session, workspace_id)
    if latest is None:
        raise WorkspaceInvalidStateError("Workspace has no configuration version")
    next_version = latest + 1
    now = datetime.now(timezone.utc)
    config_json = runtime.to_config_dict()

    ws.status = WorkspaceStatus.UPDATING.value
    ws.updated_at = now
    session.add(ws)

    cfg = WorkspaceConfig(
        workspace_id=workspace_id,
        version=next_version,
        config_json=config_json,
    )
    session.add(cfg)
    session.flush()

    cid = _effective_correlation_id(correlation_id)
    job = WorkspaceJob(
        workspace_id=workspace_id,
        job_type=WorkspaceJobType.UPDATE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=next_version,
        attempt=0,
        correlation_id=cid,
    )
    session.add(job)
    session.flush()

    record_job_queued(job_type=WorkspaceJobType.UPDATE.value)
    assert job.workspace_job_id is not None
    log_event(
        logger,
        LogEvent.WORKSPACE_JOB_QUEUED,
        correlation_id=cid,
        workspace_id=workspace_id,
        workspace_job_id=job.workspace_job_id,
        job_type=WorkspaceJobType.UPDATE.value,
    )

    record_workspace_event(
        session,
        workspace_id=workspace_id,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=ws.status,
        message="Update intent accepted; job queued",
        payload={
            "job_id": job.workspace_job_id,
            "job_type": WorkspaceJobType.UPDATE.value,
            "requested_config_version": next_version,
        },
    )

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(ws)
    session.refresh(job)
    assert job.workspace_job_id is not None

    return WorkspaceIntentResult(
        workspace_id=workspace_id,
        accepted=True,
        status=ws.status,
        job_id=job.workspace_job_id,
        job_type=WorkspaceJobType.UPDATE.value,
        requested_config_version=next_version,
        issues=(),
    )


def _check_reconcile_lease(session: Session, workspace_id: int) -> None:
    """Raise ``WorkspaceBusyError`` when a non-stale reconcile job is already pending.

    Logic:
    - If a QUEUED RECONCILE_RUNTIME job exists → lease held, skip.
    - If a RUNNING RECONCILE_RUNTIME job exists AND its ``started_at`` is within the
      configured TTL → lease held, skip.
    - If a RUNNING RECONCILE_RUNTIME job exists BUT ``started_at`` is older than the TTL →
      log as stale (crashed worker) and allow re-enqueue so the reconcile isn't blocked forever.
    """
    from datetime import timedelta  # noqa: PLC0415
    from app.libs.common.config import get_settings  # noqa: PLC0415
    from app.libs.observability.log_events import LogEvent, log_event  # noqa: PLC0415

    _lease_logger = logging.getLogger(__name__)

    existing = session.exec(
        select(WorkspaceJob).where(
            WorkspaceJob.workspace_id == workspace_id,
            WorkspaceJob.job_type == WorkspaceJobType.RECONCILE_RUNTIME.value,
            WorkspaceJob.status.in_([  # type: ignore[attr-defined]
                WorkspaceJobStatus.QUEUED.value,
                WorkspaceJobStatus.RUNNING.value,
            ]),
        )
    ).first()

    if existing is None:
        return  # No pending reconcile — proceed.

    if existing.status == WorkspaceJobStatus.QUEUED.value:
        log_event(
            _lease_logger,
            LogEvent.RECONCILE_LEASE_HELD,
            workspace_id=workspace_id,
            existing_job_id=existing.workspace_job_id,
            existing_status=existing.status,
        )
        raise WorkspaceBusyError(
            f"A RECONCILE_RUNTIME job is already queued (job_id={existing.workspace_job_id}). "
            "Skip or wait until it completes."
        )

    # RUNNING: check for staleness.
    lease_ttl = int(getattr(get_settings(), "devnest_reconcile_lease_ttl_seconds", 120))
    now_utc = datetime.now(timezone.utc)
    started = existing.started_at
    if started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_seconds = (now_utc - started).total_seconds()
        if age_seconds < lease_ttl:
            log_event(
                _lease_logger,
                LogEvent.RECONCILE_LEASE_HELD,
                workspace_id=workspace_id,
                existing_job_id=existing.workspace_job_id,
                existing_status=existing.status,
                age_seconds=int(age_seconds),
                lease_ttl=lease_ttl,
            )
            raise WorkspaceBusyError(
                f"A RECONCILE_RUNTIME job is already running (job_id={existing.workspace_job_id}, "
                f"age={int(age_seconds)}s < lease_ttl={lease_ttl}s)."
            )
        # Stale: allow re-enqueue.
        log_event(
            _lease_logger,
            LogEvent.RECONCILE_LEASE_STALE,
            workspace_id=workspace_id,
            existing_job_id=existing.workspace_job_id,
            age_seconds=int(age_seconds),
            lease_ttl=lease_ttl,
        )


def enqueue_reconcile_runtime_job(
    session: Session,
    *,
    workspace_id: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    """
    Queue a RECONCILE_RUNTIME job without changing ``Workspace.status``.

    Internal / operator use: compare desired (persisted status) to actual (orchestrator + gateway).

    Duplicate prevention (reconcile lease):
        Before inserting, :func:`_check_reconcile_lease` verifies no active RECONCILE_RUNTIME
        job exists for the workspace (QUEUED, or RUNNING within the lease TTL). This is the
        DB-level lock/lease mechanism — raises ``WorkspaceBusyError`` when the lease is held.
    """
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise WorkspaceNotFoundError("Workspace not found")
    _require_not_busy(ws)
    if ws.status not in (
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.ERROR.value,
        WorkspaceStatus.DELETED.value,
    ):
        raise WorkspaceInvalidStateError(
            f"Reconcile is only allowed when running, stopped, error, or deleted (current={ws.status})",
        )
    # Check reconcile lease before inserting (Task 2: reconcile lock/lease hardening).
    _check_reconcile_lease(session, workspace_id)
    cfg_v = _intent_config_version(session, workspace_id)
    assert ws.workspace_id is not None
    owner_id = int(ws.owner_user_id)

    cid = _effective_correlation_id(correlation_id)
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_id,
        requested_config_version=cfg_v,
        attempt=0,
        correlation_id=cid,
    )
    session.add(job)
    session.flush()

    record_job_queued(job_type=WorkspaceJobType.RECONCILE_RUNTIME.value)
    assert job.workspace_job_id is not None
    log_event(
        logger,
        LogEvent.WORKSPACE_JOB_QUEUED,
        correlation_id=cid,
        workspace_id=ws.workspace_id,
        workspace_job_id=job.workspace_job_id,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
    )

    record_workspace_event(
        session,
        workspace_id=ws.workspace_id,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=ws.status,
        message="Reconcile job queued",
        payload={
            "job_id": job.workspace_job_id,
            "job_type": WorkspaceJobType.RECONCILE_RUNTIME.value,
            "requested_config_version": cfg_v,
        },
    )

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(ws)
    session.refresh(job)
    assert job.workspace_job_id is not None

    return WorkspaceIntentResult(
        workspace_id=ws.workspace_id,
        accepted=True,
        status=ws.status,
        job_id=job.workspace_job_id,
        job_type=WorkspaceJobType.RECONCILE_RUNTIME.value,
        requested_config_version=cfg_v,
        issues=(),
    )


def create_workspace(
    session: Session,
    *,
    owner_user_id: int,
    body: CreateWorkspaceRequest,
    correlation_id: str | None = None,
) -> CreateWorkspaceResult:
    # --- Policy and quota checks (before any DB writes) ---
    cid_pre = _effective_correlation_id(correlation_id)
    check_workspace_quota(session, owner_user_id=owner_user_id, correlation_id=cid_pre)
    check_running_workspace_quota(session, owner_user_id=owner_user_id, correlation_id=cid_pre)
    check_monthly_runtime_hours_quota(session, owner_user_id=owner_user_id, correlation_id=cid_pre)
    rt_cpu = float(body.runtime.cpu_limit_cores or DEFAULT_WORKSPACE_REQUEST_CPU)
    rt_mem = int(body.runtime.memory_limit_mib or DEFAULT_WORKSPACE_REQUEST_MEMORY_MB)
    check_owner_compute_quota(
        session,
        owner_user_id=owner_user_id,
        proposed_cpu=rt_cpu,
        proposed_memory_mb=rt_mem,
        ignore_workspace_id=None,
        correlation_id=cid_pre,
    )
    evaluate_workspace_creation(
        session,
        owner_user_id=owner_user_id,
        image=body.runtime.image if body.runtime else None,
        is_private=body.is_private if body.is_private is not None else True,
        correlation_id=cid_pre,
    )

    now = datetime.now(timezone.utc)
    config_json = body.runtime.to_config_dict()

    ws = Workspace(
        name=body.name,
        description=body.description,
        owner_user_id=owner_user_id,
        project_storage_key=uuid4().hex,
        status=WorkspaceStatus.CREATING.value,
        is_private=body.is_private,
        created_at=now,
        updated_at=now,
    )
    session.add(ws)
    session.flush()

    cfg = WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json=config_json)
    session.add(cfg)
    session.flush()

    cid = _effective_correlation_id(correlation_id)
    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=WorkspaceJobType.CREATE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_user_id,
        requested_config_version=1,
        attempt=0,
        correlation_id=cid,
    )
    session.add(job)
    session.flush()

    record_job_queued(job_type=WorkspaceJobType.CREATE.value)
    assert job.workspace_job_id is not None
    assert ws.workspace_id is not None
    log_event(
        logger,
        LogEvent.WORKSPACE_INTENT_CREATED,
        correlation_id=cid,
        workspace_id=ws.workspace_id,
        workspace_job_id=job.workspace_job_id,
        job_type=WorkspaceJobType.CREATE.value,
    )
    log_event(
        logger,
        LogEvent.WORKSPACE_JOB_QUEUED,
        correlation_id=cid,
        workspace_id=ws.workspace_id,
        workspace_job_id=job.workspace_job_id,
        job_type=WorkspaceJobType.CREATE.value,
    )

    record_workspace_event(
        session,
        workspace_id=ws.workspace_id,
        event_type=WorkspaceStreamEventType.INTENT_QUEUED,
        status=ws.status,
        message="Workspace creation accepted; job queued",
        payload={
            "job_id": job.workspace_job_id,
            "job_type": WorkspaceJobType.CREATE.value,
            "requested_config_version": 1,
        },
    )

    record_audit(
        session,
        action=AuditAction.WORKSPACE_CREATE_REQUESTED.value,
        resource_type="workspace",
        resource_id=ws.workspace_id,
        actor_user_id=owner_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=ws.workspace_id,
        job_id=job.workspace_job_id,
        correlation_id=cid,
        metadata={"name": ws.name},
    )
    record_usage(
        session,
        workspace_id=int(ws.workspace_id),
        owner_user_id=owner_user_id,
        event_type=UsageEventType.WORKSPACE_CREATED.value,
        correlation_id=cid,
    )

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(ws)
    session.refresh(job)

    assert ws.workspace_id is not None
    assert job.workspace_job_id is not None

    return CreateWorkspaceResult(
        workspace_id=ws.workspace_id,
        job_id=job.workspace_job_id,
        config_version=1,
        status=ws.status,
    )


def list_workspaces(
    session: Session,
    *,
    owner_user_id: int,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[WorkspaceSummaryResponse], int]:
    where_owner = Workspace.owner_user_id == owner_user_id
    count_stmt = select(func.count()).select_from(Workspace).where(where_owner)
    total = session.exec(count_stmt).one()

    page_stmt = (
        select(Workspace)
        .where(where_owner)
        .order_by(Workspace.created_at.desc())
        .offset(skip)
        .limit(min(limit, 500))
    )
    rows = list(session.exec(page_stmt).all())
    items = [WorkspaceSummaryResponse.model_validate(r) for r in rows]
    return items, int(total)


def get_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
) -> WorkspaceDetailResponse | None:
    stmt = select(Workspace).where(
        Workspace.workspace_id == workspace_id,
        Workspace.owner_user_id == owner_user_id,
    )
    ws = session.exec(stmt).first()
    if ws is None:
        return None
    latest = _latest_config_version(session, workspace_id)
    base = WorkspaceDetailResponse.model_validate(ws)
    return base.model_copy(update={"latest_config_version": latest})
