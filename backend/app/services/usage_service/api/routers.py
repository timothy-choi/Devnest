"""Internal admin usage summary endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope

from ..service import get_user_usage_summary, get_workspace_usage_summary
from .schemas import UserUsageSummaryResponse, WorkspaceUsageSummaryResponse

router = APIRouter(
    prefix="/internal/usage",
    tags=["usage"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceUsageSummaryResponse)
def get_workspace_usage(
    workspace_id: int,
    session: Session = Depends(get_db),
) -> WorkspaceUsageSummaryResponse:
    summary = get_workspace_usage_summary(session, workspace_id=workspace_id)
    return WorkspaceUsageSummaryResponse(
        workspace_id=summary.workspace_id,
        owner_user_id=summary.owner_user_id,
        totals=summary.totals,
    )


@router.get("/users/{user_id}", response_model=UserUsageSummaryResponse)
def get_user_usage(
    user_id: int,
    session: Session = Depends(get_db),
) -> UserUsageSummaryResponse:
    summary = get_user_usage_summary(session, owner_user_id=user_id)
    return UserUsageSummaryResponse(
        owner_user_id=summary.owner_user_id,
        totals_by_event=summary.totals_by_event,
    )
