"""Internal route: enqueue RECONCILE_RUNTIME (no workspace status transition)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.services.notification_service.api.dependencies import require_internal_api_key
from app.services.workspace_service.api.schemas.workspace_schemas import WorkspaceIntentAcceptedResponse
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
)
from app.services.workspace_service.services import workspace_intent_service

router = APIRouter(
    prefix="/internal/workspaces",
    tags=["internal-workspaces"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.post(
    "/{workspace_id}/reconcile-runtime",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue runtime/gateway reconcile job",
    description=(
        "Queues RECONCILE_RUNTIME without changing workspace status. "
        "Process the job via POST /internal/workspace-jobs/process."
    ),
)
def post_enqueue_reconcile_runtime(
    workspace_id: int,
    session: Session = Depends(get_db),
) -> WorkspaceIntentAcceptedResponse:
    try:
        out = workspace_intent_service.enqueue_reconcile_runtime_job(session, workspace_id=workspace_id)
    except WorkspaceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except WorkspaceBusyError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except WorkspaceInvalidStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return WorkspaceIntentAcceptedResponse(
        workspace_id=out.workspace_id,
        status=out.status,
        job_id=out.job_id,
        job_type=out.job_type,
        requested_config_version=out.requested_config_version,
        issues=list(out.issues),
    )
