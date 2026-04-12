"""Internal routes: execute queued workspace jobs (orchestrator + DB persistence).

Requires ``X-Internal-API-Key`` scoped to workspace job execution (or legacy ``INTERNAL_API_KEY``).
Does **not** enqueue jobs;
Workspace Service intent routes remain the sole creator of ``WorkspaceJob`` rows.

V1: explicit trigger for job execution until a background poller or external queue exists.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope
from app.services.orchestrator_service.errors import AppOrchestratorBindingError
from app.services.workspace_service.api.schemas.internal_workspace_jobs import (
    ProcessWorkspaceJobsResponse,
)
from app.workers.workspace_job_runner import execute_workspace_job_tick_with_default_orchestrator

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/workspace-jobs",
    tags=["internal-workspace-jobs"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.WORKSPACE_JOBS))],
)


@router.post(
    "/process",
    response_model=ProcessWorkspaceJobsResponse,
    status_code=status.HTTP_200_OK,
    summary="Process queued workspace jobs (internal)",
    description=(
        "Runs the workspace job worker against the Docker-backed orchestrator for up to ``limit`` "
        "queued jobs (FIFO, row-locked dequeue), or a single ``job_id`` if ``QUEUED``. Each job "
        "commits in its own session; the request session commit is a no-op if unused."
    ),
)
def post_process_workspace_jobs(
    request: Request,
    session: Session = Depends(get_db),
    limit: int = Query(1, ge=1, le=50, description="Max queued jobs to process when job_id is omitted."),
    job_id: int | None = Query(
        default=None,
        description="If set, only this job is executed when it is QUEUED.",
    ),
) -> ProcessWorkspaceJobsResponse:
    cid = getattr(request.state, "correlation_id", None)
    log_event(
        _logger,
        LogEvent.AUDIT_INTERNAL_WORKSPACE_JOBS_PROCESS,
        correlation_id=cid,
        limit=limit,
        job_id=job_id,
    )
    try:
        tick = execute_workspace_job_tick_with_default_orchestrator(
            session,
            limit=limit,
            workspace_job_id=job_id,
        )
    except AppOrchestratorBindingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return ProcessWorkspaceJobsResponse(
        processed_count=tick.processed_count,
        last_job_id=tick.last_job_id,
    )
