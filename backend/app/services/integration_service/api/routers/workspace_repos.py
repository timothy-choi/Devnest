"""Workspace-scoped repository import and Git synchronization routes.

Architecture decision: Git operations (clone, pull, push) execute *inside*
the workspace container using the same NodeExecutionBundle that the orchestrator
uses for lifecycle jobs:

- ``POST /workspaces/{id}/import-repo`` — asynchronous clone via REPO_IMPORT
  worker job; returns 202 with ``repo_id`` + ``job_id``.
- ``GET  /workspaces/{id}/repo`` — fetch current repo metadata.
- ``POST /workspaces/{id}/git/pull`` — synchronous pull (≤ 60s); 200 with output.
- ``POST /workspaces/{id}/git/push`` — synchronous push (≤ 60s); 200 with output.

All routes require JWT auth and validate workspace ownership. Git operations also
require the workspace to be in RUNNING state and the runtime container to exist.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.libs.db.database import get_db
from app.libs.observability.correlation import get_correlation_id
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.integration_service.api.routers.provider_tokens import resolve_provider_token
from app.services.integration_service.api.schemas import (
    GitOperationResponse,
    GitPullRequest,
    GitPushRequest,
    ImportRepoRequest,
    ImportRepoResponse,
    RepoStatusResponse,
)
from app.services.integration_service.git_executor import (
    GitExecutionError,
    run_git_in_container,
)
from app.services.integration_service.models import WorkspaceRepository
from app.services.node_execution_service.factory import resolve_node_execution_bundle
from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceJobType, WorkspaceJobStatus, WorkspaceStatus
from app.services.workspace_service.models.workspace_job import WorkspaceJob

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["workspace-integrations"])

_GIT_SYNC_TIMEOUT = 60  # seconds for synchronous pull/push


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_workspace_owned(session: Session, workspace_id: int, user_id: int) -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if ws.owner_user_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your workspace")
    return ws


def _get_running_runtime(session: Session, workspace_id: int) -> WorkspaceRuntime:
    """Return WorkspaceRuntime for a RUNNING workspace; raises 409 if not ready."""
    ws = session.exec(
        select(Workspace).where(Workspace.workspace_id == workspace_id)
    ).first()
    if ws is None or ws.status != WorkspaceStatus.RUNNING.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Workspace must be RUNNING for git operations (current: {ws.status if ws else 'not_found'})",
        )
    runtime = session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
    ).first()
    if runtime is None or not runtime.container_id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Workspace runtime container not ready")
    return runtime


def _get_repo(session: Session, workspace_id: int) -> WorkspaceRepository | None:
    return session.exec(
        select(WorkspaceRepository).where(WorkspaceRepository.workspace_id == workspace_id)
    ).first()


def _enqueue_repo_import_job(session: Session, repo: WorkspaceRepository) -> int:
    """Enqueue a REPO_IMPORT worker job and return the job id."""
    from app.services.workspace_service.models.workspace_config import WorkspaceConfig  # noqa: PLC0415

    cfg = session.exec(
        select(WorkspaceConfig).where(WorkspaceConfig.workspace_id == repo.workspace_id)
        .order_by(WorkspaceConfig.version.desc())
    ).first()
    config_version = cfg.version if cfg else 1

    job = WorkspaceJob(
        workspace_id=repo.workspace_id,
        job_type=WorkspaceJobType.REPO_IMPORT.value,
        status=WorkspaceJobStatus.QUEUED.value,
        requested_by_user_id=repo.owner_user_id,
        requested_config_version=config_version,
    )
    session.add(job)
    session.flush()
    return int(job.workspace_job_id)


# ── Import ────────────────────────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/import-repo",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ImportRepoResponse,
    summary="Clone a repository into the workspace container (async)",
)
def import_repo(
    workspace_id: int,
    body: ImportRepoRequest,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ImportRepoResponse:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)

    existing = _get_repo(session, workspace_id)
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Workspace already has an associated repository. DELETE it before importing a new one.",
        )

    now = datetime.now(timezone.utc)
    repo = WorkspaceRepository(
        workspace_id=workspace_id,
        owner_user_id=current.user_auth_id,
        repo_url=body.repo_url,
        branch=body.branch,
        clone_dir=body.clone_dir,
        provider=body.use_provider,
        clone_status="pending",
        created_at=now,
        updated_at=now,
    )
    if body.use_provider:
        repo.provider = body.use_provider

    # Derive provider_repo_name from GitHub URL if possible.
    url = body.repo_url
    if "github.com/" in url:
        parts = url.rstrip("/").split("github.com/")[-1].removesuffix(".git")
        repo.provider_repo_name = parts if "/" in parts else None
        if not repo.provider:
            repo.provider = "github"

    session.add(repo)
    session.flush()

    job_id = _enqueue_repo_import_job(session, repo)
    repo.last_job_id = job_id
    session.add(repo)
    session.commit()
    session.refresh(repo)

    return ImportRepoResponse(
        repo_id=int(repo.repo_id),
        workspace_id=workspace_id,
        repo_url=repo.repo_url,
        branch=repo.branch,
        clone_dir=repo.clone_dir,
        clone_status=repo.clone_status,
        job_id=job_id,
    )


@router.get(
    "/{workspace_id}/repo",
    response_model=RepoStatusResponse,
    summary="Get workspace repository metadata and clone status",
)
def get_repo_status(
    workspace_id: int,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> RepoStatusResponse:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    repo = _get_repo(session, workspace_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No repository associated with this workspace")

    return RepoStatusResponse(
        repo_id=int(repo.repo_id),
        workspace_id=workspace_id,
        repo_url=repo.repo_url,
        branch=repo.branch,
        clone_dir=repo.clone_dir,
        clone_status=repo.clone_status,
        last_synced_at=repo.last_synced_at,
        error_msg=repo.error_msg,
    )


@router.delete(
    "/{workspace_id}/repo",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Remove repository association (does NOT delete files in container)",
)
def delete_repo(
    workspace_id: int,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    repo = _get_repo(session, workspace_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No repository associated with this workspace")
    session.delete(repo)
    session.commit()


# ── Git pull / push ───────────────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/git/pull",
    response_model=GitOperationResponse,
    summary="Pull latest changes for the workspace repository",
)
async def git_pull(
    workspace_id: int,
    body: GitPullRequest,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> GitOperationResponse:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    # Check repo existence first — gives 404 even if workspace is not yet RUNNING.
    repo = _get_repo(session, workspace_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No repository associated; call import-repo first")
    if repo.clone_status != "cloned":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Repository is not yet cloned (status={repo.clone_status})",
        )
    # Runtime check comes after: workspace must be RUNNING to exec git inside the container.
    runtime = _get_running_runtime(session, workspace_id)

    provider_token = None
    provider = body.use_provider or repo.provider
    if provider:
        provider_token = resolve_provider_token(session, current.user_auth_id, provider)

    bundle = resolve_node_execution_bundle(session, runtime.node_id)
    branch = body.branch or repo.branch
    git_args = ["pull", body.remote, branch]

    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: run_git_in_container(
                    bundle, runtime.container_id, git_args,
                    workdir=repo.clone_dir, provider_token=provider_token,
                    timeout_seconds=_GIT_SYNC_TIMEOUT,
                ),
            ),
            timeout=_GIT_SYNC_TIMEOUT + 5.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail="git pull timed out") from None
    except GitExecutionError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if result.success:
        now = datetime.now(timezone.utc)
        repo.last_synced_at = now
        repo.updated_at = now
        session.add(repo)
        session.commit()

    return GitOperationResponse(
        success=result.success,
        exit_code=result.exit_code,
        output=result.output,
        operation="pull",
        repo_url=repo.repo_url,
    )


@router.post(
    "/{workspace_id}/git/push",
    response_model=GitOperationResponse,
    summary="Push local commits from the workspace repository",
)
async def git_push(
    workspace_id: int,
    body: GitPushRequest,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> GitOperationResponse:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    # Check repo existence first — gives 404 even if workspace is not yet RUNNING.
    repo = _get_repo(session, workspace_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No repository associated; call import-repo first")
    if repo.clone_status != "cloned":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Repository is not yet cloned (status={repo.clone_status})",
        )
    # Runtime check comes after: workspace must be RUNNING to exec git inside the container.
    runtime = _get_running_runtime(session, workspace_id)

    provider_token = None
    provider = body.use_provider or repo.provider
    if provider:
        provider_token = resolve_provider_token(session, current.user_auth_id, provider)

    bundle = resolve_node_execution_bundle(session, runtime.node_id)
    branch = body.branch or repo.branch
    git_args = ["push", body.remote, branch]
    if body.force:
        git_args.append("--force-with-lease")

    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: run_git_in_container(
                    bundle, runtime.container_id, git_args,
                    workdir=repo.clone_dir, provider_token=provider_token,
                    timeout_seconds=_GIT_SYNC_TIMEOUT,
                ),
            ),
            timeout=_GIT_SYNC_TIMEOUT + 5.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail="git push timed out") from None
    except GitExecutionError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return GitOperationResponse(
        success=result.success,
        exit_code=result.exit_code,
        output=result.output,
        operation="push",
        repo_url=repo.repo_url,
    )
