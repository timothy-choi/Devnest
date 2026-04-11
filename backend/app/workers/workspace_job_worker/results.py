"""Result types for workspace job worker ticks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceJobWorkerTickResult:
    """Outcome of attempting to process queued workspace jobs once."""

    processed_count: int
    """Number of jobs taken from ``QUEUED`` through success/failure finalization in this call."""

    last_job_id: int | None
    """Primary key of the last finalized job, if any."""
