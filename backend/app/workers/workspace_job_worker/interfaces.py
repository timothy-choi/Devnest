"""Contracts for workspace job execution (optional typing aid)."""

from __future__ import annotations

from typing import Protocol

from sqlmodel import Session

from app.services.orchestrator_service.interfaces import OrchestratorService

from .results import WorkspaceJobWorkerTickResult


class WorkspaceJobWorker(Protocol):
    def run_pending_jobs(
        self,
        session: Session,
        orchestrator: OrchestratorService,
        *,
        limit: int = 1,
    ) -> WorkspaceJobWorkerTickResult:
        """Load up to ``limit`` queued jobs and run them sequentially."""
        ...
