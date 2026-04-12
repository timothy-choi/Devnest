"""User-facing snapshot APIs (async jobs via workspace worker)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas.snapshot_schemas import (
    CreateSnapshotAcceptedResponse,
    CreateSnapshotRequest,
    RestoreSnapshotAcceptedResponse,
    SnapshotSummaryResponse,
)
from app.services.workspace_service.errors import (
    SnapshotConflictError,
    SnapshotNotFoundError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from app.services.workspace_service.models import WorkspaceSnapshot
from app.services.workspace_service.services import snapshot_service

workspace_snapshots_router = APIRouter(prefix="/workspaces", tags=["snapshots"])
snapshots_router = APIRouter(prefix="/snapshots", tags=["snapshots"])


def _correlation_id_from_request(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _raise_snapshot_http(exc: WorkspaceServiceError) -> None:
    if isinstance(exc, (WorkspaceNotFoundError, SnapshotNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, SnapshotConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, WorkspaceInvalidStateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _summary(row: WorkspaceSnapshot) -> SnapshotSummaryResponse:
    assert row.workspace_snapshot_id is not None
    assert row.workspace_id is not None
    return SnapshotSummaryResponse(
        workspace_snapshot_id=row.workspace_snapshot_id,
        workspace_id=row.workspace_id,
        name=row.name,
        description=row.description,
        status=row.status,
        size_bytes=row.size_bytes,
        storage_uri=row.storage_uri,
        created_at=row.created_at,
        metadata=row.metadata_json,
    )


@workspace_snapshots_router.post(
    "/{workspace_id}/snapshots",
    response_model=CreateSnapshotAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create workspace snapshot (queued)",
)
def post_workspace_snapshot(
    request: Request,
    workspace_id: int,
    body: CreateSnapshotRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> CreateSnapshotAcceptedResponse:
    assert current.user_auth_id is not None
    try:
        out = snapshot_service.create_snapshot(
            session,
            workspace_id=workspace_id,
            owner_user_id=current.user_auth_id,
            name=body.name,
            description=body.description,
            metadata=body.metadata,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as e:
        _raise_snapshot_http(e)
    return CreateSnapshotAcceptedResponse(
        workspace_id=out.workspace_id,
        snapshot_id=out.snapshot_id,
        job_id=out.job_id,
        status=out.status,
    )


@workspace_snapshots_router.get(
    "/{workspace_id}/snapshots",
    response_model=list[SnapshotSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="List workspace snapshots",
)
def get_workspace_snapshots(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> list[SnapshotSummaryResponse]:
    assert current.user_auth_id is not None
    try:
        rows = snapshot_service.list_snapshots(
            session,
            workspace_id=workspace_id,
            owner_user_id=current.user_auth_id,
        )
    except WorkspaceServiceError as e:
        _raise_snapshot_http(e)
    return [_summary(r) for r in rows]


@snapshots_router.get(
    "/{snapshot_id}",
    response_model=SnapshotSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get snapshot details",
)
def get_snapshot_by_id(
    snapshot_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> SnapshotSummaryResponse:
    assert current.user_auth_id is not None
    try:
        row = snapshot_service.get_snapshot(
            session,
            snapshot_id=snapshot_id,
            owner_user_id=current.user_auth_id,
        )
    except WorkspaceServiceError as e:
        _raise_snapshot_http(e)
    return _summary(row)


@snapshots_router.post(
    "/{snapshot_id}/restore",
    response_model=RestoreSnapshotAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore workspace files from snapshot (queued; workspace must be STOPPED)",
)
def post_snapshot_restore(
    request: Request,
    snapshot_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> RestoreSnapshotAcceptedResponse:
    assert current.user_auth_id is not None
    try:
        out = snapshot_service.restore_snapshot(
            session,
            snapshot_id=snapshot_id,
            owner_user_id=current.user_auth_id,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as e:
        _raise_snapshot_http(e)
    return RestoreSnapshotAcceptedResponse(
        workspace_id=out.workspace_id,
        snapshot_id=out.snapshot_id,
        job_id=out.job_id,
        workspace_status=out.status,
    )


@snapshots_router.delete(
    "/{snapshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete snapshot metadata and archive",
)
def delete_snapshot_by_id(
    snapshot_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> Response:
    assert current.user_auth_id is not None
    try:
        snapshot_service.delete_snapshot(
            session,
            snapshot_id=snapshot_id,
            owner_user_id=current.user_auth_id,
        )
    except WorkspaceServiceError as e:
        _raise_snapshot_http(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
