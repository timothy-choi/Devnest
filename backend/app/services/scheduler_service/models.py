"""Scheduler dataclasses (control plane; no ORM)."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.placement_service.models import ExecutionNode


@dataclass(frozen=True)
class WorkspaceComputeRequest:
    """Resource shape used for filter-only placement (V1; no persistent accounting)."""

    requested_cpu: float
    requested_memory_mb: int


@dataclass(frozen=True)
class WorkspaceScheduleResult:
    """Outcome of :func:`~app.services.scheduler_service.service.schedule_workspace`."""

    execution_node: ExecutionNode | None
    insufficient_capacity: bool
    invalid_request: bool
    message: str
