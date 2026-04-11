"""Workspace control-plane intent: metadata rows and queued jobs (no orchestrator)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.services.workspace_service.api.schemas import (
    CreateWorkspaceRequest,
    WorkspaceDetailResponse,
    WorkspaceSummaryResponse,
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


def _latest_config_version(session: Session, workspace_id: int) -> int | None:
    stmt = (
        select(WorkspaceConfig)
        .where(WorkspaceConfig.workspace_id == workspace_id)
        .order_by(WorkspaceConfig.version.desc())
        .limit(1)
    )
    cfg = session.exec(stmt).first()
    return cfg.version if cfg is not None else None


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
