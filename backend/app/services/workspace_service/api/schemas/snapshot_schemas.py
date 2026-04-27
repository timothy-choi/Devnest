"""HTTP schemas for workspace snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SnapshotStorageBackend = Literal["s3", "local", "pending", "unknown"]


def storage_backend_label(storage_uri: str) -> SnapshotStorageBackend:
    """Map persisted ``storage_uri`` to a coarse label for API responses (no bucket paths or keys)."""
    u = (storage_uri or "").strip().lower()
    if u.startswith("s3://"):
        return "s3"
    if u.startswith("file://"):
        return "local"
    if u in ("", "pending"):
        return "pending"
    return "unknown"


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
    storage_backend: SnapshotStorageBackend = Field(
        description="Where the archive is stored: s3, local filesystem, pending placement, or unknown.",
    )
    created_at: datetime
    metadata: dict | None = None


class RestoreSnapshotAcceptedResponse(BaseModel):
    workspace_id: int
    snapshot_id: int
    job_id: int
    workspace_status: str


class SnapshotArchiveDownloadOfferResponse(BaseModel):
    """JSON-only download instructions (presigned S3 or backend URL + token for local)."""

    mode: Literal["presigned_s3", "backend_direct"]
    filename: str
    expires_in: int
    presigned_url: str | None = None
    relative_url: str | None = None
