"""Application entrypoint: run queued workspace jobs against a real orchestrator and commit.

Workspace intent APIs enqueue ``WorkspaceJob`` rows only; this module performs execution by
delegating to :mod:`app.workers.workspace_job_worker.worker` with a process-local orchestrator.

V1: intended to be invoked from an internal HTTP route, a future background poller, or tests —
not from Workspace Service intent methods (preserves separation of control plane vs execution).
"""

from __future__ import annotations

from sqlmodel import Session

from app.services.orchestrator_service.app_factory import build_default_orchestrator_for_session
from app.services.orchestrator_service.errors import AppOrchestratorBindingError
from app.services.orchestrator_service.interfaces import OrchestratorService

from .workspace_job_worker.results import WorkspaceJobWorkerTickResult
from .workspace_job_worker.worker import (
    run_pending_jobs,
    run_queued_workspace_job_by_id,
)


def execute_workspace_job_tick(
    session: Session,
    orchestrator: OrchestratorService,
    *,
    limit: int = 1,
    workspace_job_id: int | None = None,
) -> WorkspaceJobWorkerTickResult:
    """
    Run queued job(s) in ``session`` and **commit** on success.

    If ``workspace_job_id`` is set, only that job is considered (and only if ``QUEUED``).
    Otherwise the oldest ``limit`` queued job(s) are processed.

    Raises:
        Any exception from the orchestrator or DB after ``session.rollback()``.
    """
    try:
        if workspace_job_id is not None:
            tick = run_queued_workspace_job_by_id(
                session,
                orchestrator,
                workspace_job_id=workspace_job_id,
            )
        else:
            tick = run_pending_jobs(session, orchestrator, limit=limit)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return tick


def execute_workspace_job_tick_with_default_orchestrator(
    session: Session,
    *,
    limit: int = 1,
    workspace_job_id: int | None = None,
) -> WorkspaceJobWorkerTickResult:
    """
    Same as :func:`execute_workspace_job_tick` but builds :func:`build_default_orchestrator_for_session`.

    Raises:
        AppOrchestratorBindingError: propagated (caller maps to HTTP 503, etc.).
    """
    orchestrator = build_default_orchestrator_for_session(session)
    return execute_workspace_job_tick(
        session,
        orchestrator,
        limit=limit,
        workspace_job_id=workspace_job_id,
    )
