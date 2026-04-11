"""Workspace control-plane routes (V1: create, list, get, lifecycle intents)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas import (
    CreateWorkspaceAcceptedResponse,
    CreateWorkspaceRequest,
    PatchWorkspaceUpdateRequest,
    WorkspaceDetailResponse,
    WorkspaceIntentAcceptedResponse,
    WorkspaceListResponse,
)
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from app.services.workspace_service.services import workspace_intent_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _raise_workspace_http(exc: WorkspaceServiceError) -> None:
    if isinstance(exc, WorkspaceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc
    if isinstance(exc, WorkspaceBusyError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, WorkspaceInvalidStateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _intent_response(
    out: workspace_intent_service.WorkspaceIntentResult,
) -> WorkspaceIntentAcceptedResponse:
    return WorkspaceIntentAcceptedResponse(
        workspace_id=out.workspace_id,
        status=out.status,
        job_id=out.job_id,
        job_type=out.job_type,
        requested_config_version=out.requested_config_version,
        issues=list(out.issues),
    )


@router.post(
    "",
    response_model=CreateWorkspaceAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create workspace (accepted)",
)
def post_workspace(
    body: CreateWorkspaceRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> CreateWorkspaceAcceptedResponse:
    assert current.user_auth_id is not None
    out = workspace_intent_service.create_workspace(
        session,
        owner_user_id=current.user_auth_id,
        body=body,
    )
    return CreateWorkspaceAcceptedResponse(
        workspace_id=out.workspace_id,
        status=out.status,
        config_version=out.config_version,
        job_id=out.job_id,
    )


@router.get(
    "",
    response_model=WorkspaceListResponse,
    status_code=status.HTTP_200_OK,
    summary="List workspaces for the current user",
)
def get_workspaces(
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> WorkspaceListResponse:
    assert current.user_auth_id is not None
    items, total = workspace_intent_service.list_workspaces(
        session,
        owner_user_id=current.user_auth_id,
        skip=skip,
        limit=limit,
    )
    return WorkspaceListResponse(items=items, total=total)


@router.post(
    "/start/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace start (accepted)",
)
def post_workspace_start(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_start_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.post(
    "/stop/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace stop (accepted)",
)
def post_workspace_stop(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_stop_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.post(
    "/restart/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace restart (accepted)",
)
def post_workspace_restart(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_restart_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.delete(
    "/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace delete (accepted)",
)
def delete_workspace(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_delete_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace config update (accepted)",
)
def patch_workspace_update(
    workspace_id: int,
    body: PatchWorkspaceUpdateRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_update_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            runtime=body.runtime,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get workspace by id",
)
def get_workspace(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceDetailResponse:
    assert current.user_auth_id is not None
    detail = workspace_intent_service.get_workspace(
        session,
        workspace_id=workspace_id,
        owner_user_id=current.user_auth_id,
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return detail
