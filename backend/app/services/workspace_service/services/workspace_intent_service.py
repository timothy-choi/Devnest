"""Workspace control-plane intent: metadata rows and queued jobs (no orchestrator)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    """Attach when RUNNING + runtime placed; increments ``active_sessions_count`` (V1 stand-in for sessions)."""

    workspace_id: int
    accepted: bool
    status: str
    runtime_ready: bool
    active_sessions_count: int
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
    """Stored ``Workspace.public_host``, or default ``{id}.{base_domain}`` when gateway is on and runtime is ready."""
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
    return f"{wid}.{dom}"


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
) -> WorkspaceAccessResult:
    """
    Return normalized access fields when the workspace runtime is ready.

    Does not enqueue jobs and does not mutate rows. Read-only after auth check.
    """
    ws = _get_owned_workspace(session, workspace_id, owner_user_id)
    _ensure_workspace_running_for_access(ws)
    rt = _get_workspace_runtime(session, workspace_id)
    if not _runtime_ready_for_access(ws, rt):
        raise WorkspaceInvalidStateError(
            "Workspace is RUNNING but runtime metadata is not ready for access yet; retry shortly.",
        )
    assert rt is not None
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
) -> WorkspaceAttachResult:
    """
    Grant access when RUNNING + runtime placed: same checks as :func:`get_workspace_access`, then bump
    ``active_sessions_count`` as a V1 session surrogate (no token table yet).

    Does **not** start or provision the workspace; callers use ``POST /workspaces/start/{id}`` first.
    ``requested_by_user_id`` reserved for future audit / per-user sessions.
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
    now = datetime.now(timezone.utc)
    ws.active_sessions_count = int(ws.active_sessions_count or 0) + 1
    ws.updated_at = now
    session.add(ws)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(ws)
    return WorkspaceAttachResult(
        workspace_id=workspace_id,
        accepted=True,
        status=ws.status,
        runtime_ready=True,
        active_sessions_count=ws.active_sessions_count,
        endpoint_ref=ws.endpoint_ref,
        public_host=_resolve_public_host_for_gateway_display(ws, rt),
        internal_endpoint=rt.internal_endpoint,
        gateway_url=_derive_gateway_url_v1(ws, rt),
        issues=issues,
    )


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


def enqueue_reconcile_runtime_job(
    session: Session,
    *,
    workspace_id: int,
    correlation_id: str | None = None,
) -> WorkspaceIntentResult:
    """
    Queue a RECONCILE_RUNTIME job without changing ``Workspace.status``.

    Internal / operator use: compare desired (persisted status) to actual (orchestrator + gateway).
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
    now = datetime.now(timezone.utc)
    config_json = body.runtime.to_config_dict()

    ws = Workspace(
        name=body.name,
        description=body.description,
        owner_user_id=owner_user_id,
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
