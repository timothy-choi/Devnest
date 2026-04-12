"""HTTP schemas for workspace snapshots."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateSnapshotRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=8192)
    metadata: dict | None = None


class CreateSnapshotAcceptedResponse(BaseModel):
    workspace_id: int
    snapshot_id: int
    job_id: int
    status: str


class SnapshotSummaryResponse(BaseModel):
    workspace_snapshot_id: int
    workspace_id: int
    name: str
    description: str | None
    status: str
    size_bytes: int | None
    storage_uri: str
    created_at: datetime
    metadata: dict | None = None


class RestoreSnapshotAcceptedResponse(BaseModel):
    workspace_id: int
    snapshot_id: int
    job_id: int
    workspace_status: str
