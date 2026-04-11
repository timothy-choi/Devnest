"""Contracts for workspace job execution (optional typing aid)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlmodel import Session

from app.services.orchestrator_service.interfaces import OrchestratorService

from .results import WorkspaceJobWorkerTickResult


class WorkspaceJobWorker(Protocol):
    def run_pending_jobs(
        self,
        session: Session,
        *,
        get_orchestrator: Callable[[Session], OrchestratorService],
        limit: int = 1,
    ) -> WorkspaceJobWorkerTickResult:
        """Dequeue up to ``limit`` jobs (row-locked) and run them with a per-job orchestrator."""
        ...
