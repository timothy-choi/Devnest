"""Workspace filesystem snapshots (metadata + storage URI; archive written by worker/orchestrator)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from .enums import WorkspaceSnapshotStatus


class WorkspaceSnapshot(SQLModel, table=True):
    """Versioned filesystem backup metadata for a workspace.

    ``storage_uri`` is set to a deterministic provider URI after flush (replaces placeholder
    ``pending``). V1 restores **project files only**; ``WorkspaceConfig`` / ``WorkspaceRuntime`` are
    not rewound—operators may enqueue update/reconcile after restore.
    """

    __tablename__ = "workspace_snapshot"

    workspace_snapshot_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    name: str = Field(max_length=255, index=True)
    description: str | None = Field(default=None, max_length=8192)
    storage_uri: str = Field(
        max_length=1024,
        description="Opaque provider-specific URI (e.g. file:// path for local V1).",
    )
    status: str = Field(
        max_length=32,
        default=WorkspaceSnapshotStatus.CREATING.value,
        index=True,
    )
    size_bytes: int | None = Field(default=None, ge=0)
    created_by_user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Arbitrary snapshot metadata (tags, source ref, etc.).",
    )
