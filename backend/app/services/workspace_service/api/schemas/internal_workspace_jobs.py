"""Schemas for internal workspace job execution routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProcessWorkspaceJobsResponse(BaseModel):
    """Result of draining queued workspace jobs for one tick."""

    processed_count: int = Field(ge=0, description="Jobs moved from QUEUED through finalization in this call.")
    last_job_id: int | None = Field(
        default=None,
        description="Primary key of the last job touched, if any.",
    )
