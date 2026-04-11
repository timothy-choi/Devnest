"""Workspace control-plane routes (V1: create intent, list, get)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas import (
    CreateWorkspaceAcceptedResponse,
    CreateWorkspaceRequest,
    WorkspaceDetailResponse,
    WorkspaceListResponse,
)
from app.services.workspace_service.services import workspace_intent_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


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
