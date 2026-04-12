"""Application entrypoint: run queued workspace jobs against a real orchestrator and commit.

Workspace intent APIs enqueue ``WorkspaceJob`` rows only; this module performs execution by
delegating to :mod:`app.workers.workspace_job_worker.worker` with a process-local orchestrator.

Each processed job commits in its **own** SQL session (see worker dequeue hardening). The FastAPI
``session`` argument is only used to obtain the database bind; an outer ``commit()`` is harmless if
the request session had no pending changes.

V1: invoked from an internal HTTP route, :mod:`app.workers.workspace_job_poll_loop`, or tests —
not from Workspace Service intent methods (preserves separation of control plane vs execution).
"""

from __future__ import annotations

from sqlmodel import Session

from app.services.orchestrator_service.app_factory import build_orchestrator_for_workspace_job
from app.services.orchestrator_service.errors import AppOrchestratorBindingError

from .workspace_job_worker.results import WorkspaceJobWorkerTickResult
from .workspace_job_worker.worker import (
    run_pending_jobs,
    run_queued_workspace_job_by_id,
)


def execute_workspace_job_tick(
    session: Session,
    *,
    limit: int = 1,
    workspace_job_id: int | None = None,
) -> WorkspaceJobWorkerTickResult:
    """
    Run queued job(s); each job is committed independently inside the worker.

    If ``workspace_job_id`` is set, only that job is considered (and only if ``QUEUED`` and not
    locked by another runner).

    Raises:
        Any exception from the orchestrator or DB (request ``session`` is rolled back; completed
        job commits are not undone).
    """
    try:
        if workspace_job_id is not None:
            tick = run_queued_workspace_job_by_id(
                session,
                get_orchestrator=build_orchestrator_for_workspace_job,
                workspace_job_id=workspace_job_id,
            )
        else:
            tick = run_pending_jobs(
                session,
                get_orchestrator=build_orchestrator_for_workspace_job,
                limit=limit,
            )
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
    Same as :func:`execute_workspace_job_tick` (orchestrator is built per inner job session).

    Raises:
        AppOrchestratorBindingError: propagated (caller maps to HTTP 503, etc.).
    """
    return execute_workspace_job_tick(
        session,
        limit=limit,
        workspace_job_id=workspace_job_id,
    )
