"""Internal route: enqueue RECONCILE_RUNTIME (no workspace status transition)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.workspace_service.api.schemas.workspace_schemas import WorkspaceIntentAcceptedResponse
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
)
from app.services.workspace_service.services import workspace_intent_service

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/workspaces",
    tags=["internal-workspaces"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.WORKSPACE_RECONCILE))],
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
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
) -> WorkspaceIntentAcceptedResponse:
    cid = getattr(request.state, "correlation_id", None)
    log_event(
        _logger,
        LogEvent.AUDIT_INTERNAL_WORKSPACE_RECONCILE_RUNTIME,
        correlation_id=cid,
        workspace_id=workspace_id,
    )
    try:
        out = workspace_intent_service.enqueue_reconcile_runtime_job(
            session,
            workspace_id=workspace_id,
            correlation_id=cid,
        )
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
