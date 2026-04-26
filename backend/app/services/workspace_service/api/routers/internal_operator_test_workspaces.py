"""Internal operator routes: pinned test workspace on a specific execution node (Phase 3b Step 8).

Requires the same ``X-Internal-API-Key`` as ``/internal/execution-nodes`` (``InternalApiScope.INFRASTRUCTURE``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.workspace_service.api.schemas import (
    CreateWorkspaceAcceptedResponse,
    WorkspaceRuntimeSpecSchema,
)
from app.services.workspace_service.errors import (
    WorkspaceOperatorPinnedDisabledError,
    WorkspaceOperatorPinnedNodeInvalidError,
    WorkspaceOperatorPinnedNotAllowlistedError,
    WorkspaceServiceError,
)
from app.services.workspace_service.services import workspace_intent_service

router = APIRouter(
    prefix="/internal/test-workspaces",
    tags=["internal-test-workspaces"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


class PinnedOperatorTestCreateBody(BaseModel):
    """Body for pinned operator CREATE (name is assigned server-side)."""

    owner_user_id: int = Field(..., ge=1)
    execution_node_id: int = Field(..., ge=1)
    description: str | None = Field(default=None, max_length=8192)
    runtime: WorkspaceRuntimeSpecSchema | None = None


def _raise_workspace_operator_http(exc: WorkspaceServiceError) -> None:
    if isinstance(exc, WorkspaceOperatorPinnedDisabledError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(
        exc,
        (WorkspaceOperatorPinnedNotAllowlistedError, WorkspaceOperatorPinnedNodeInvalidError),
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/pinned-operator-create",
    response_model=CreateWorkspaceAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary=(
        "Queue pinned CREATE on an allowlisted execution_node.id (Step 8). "
        "Requires DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true and fresh target heartbeat."
    ),
)
def post_pinned_operator_test_create(
    body: PinnedOperatorTestCreateBody,
    request: Request,
    session: Session = Depends(get_db),
) -> CreateWorkspaceAcceptedResponse:
    cid = getattr(request.state, "correlation_id", None)
    try:
        out = workspace_intent_service.create_operator_pinned_test_workspace(
            session,
            owner_user_id=int(body.owner_user_id),
            execution_node_id=int(body.execution_node_id),
            runtime=body.runtime,
            description=body.description,
            correlation_id=cid if isinstance(cid, str) else None,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_operator_http(exc)
    return CreateWorkspaceAcceptedResponse(
        workspace_id=out.workspace_id,
        status=out.status,
        config_version=out.config_version,
        job_id=out.job_id,
        message="Pinned operator test workspace creation accepted.",
    )
