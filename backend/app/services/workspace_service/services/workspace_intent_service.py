"""Workspace control-plane intent: metadata rows and queued jobs (no orchestrator)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

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
    WorkspaceStatus,
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


def _intent_config_version(session: Session, workspace_id: int) -> int:
    v = _latest_config_version(session, workspace_id)
    if v is None:
        raise WorkspaceInvalidStateError("Workspace has no configuration version")
    return v


def _persist_intent(
    session: Session,
    ws: Workspace,
    *,
    new_status: str,
    job_type: str,
    requested_by_user_id: int,
    requested_config_version: int,
) -> WorkspaceIntentResult:
    now = datetime.now(timezone.utc)
    ws.status = new_status
    ws.updated_at = now
    session.add(ws)

    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=job_type,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=requested_config_version,
        attempt=0,
    )
    session.add(job)
    session.flush()

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
    )


def request_stop_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
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
    )


def request_restart_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
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
    )


def request_delete_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
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
    )


def request_update_workspace(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    requested_by_user_id: int,
    runtime: WorkspaceRuntimeSpecSchema,
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

    job = WorkspaceJob(
        workspace_id=workspace_id,
        job_type=WorkspaceJobType.UPDATE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=requested_by_user_id,
        requested_config_version=next_version,
        attempt=0,
    )
    session.add(job)
    session.flush()

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


def create_workspace(
    session: Session,
    *,
    owner_user_id: int,
    body: CreateWorkspaceRequest,
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

    job = WorkspaceJob(
        workspace_id=ws.workspace_id,
        job_type=WorkspaceJobType.CREATE.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=owner_user_id,
        requested_config_version=1,
        attempt=0,
    )
    session.add(job)
    session.flush()

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
