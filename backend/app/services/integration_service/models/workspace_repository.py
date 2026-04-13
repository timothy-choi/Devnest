"""Workspace-scoped Git repository metadata.

A workspace may have one associated repository (1:1 for V1). The repository is
cloned into the workspace container's project directory on import. Status tracks
the async clone lifecycle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class WorkspaceRepository(SQLModel, table=True):
    __tablename__ = "workspace_repository"

    repo_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    owner_user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)

    # HTTPS URL of the repository (credentials not stored here — injected at runtime).
    repo_url: str = Field(max_length=1024)
    # Branch to clone/track.
    branch: str = Field(default="main", max_length=255)
    # Absolute path inside the container where the repo is cloned.
    clone_dir: str = Field(max_length=1024)

    # Provider identifier, e.g. "github" (used to select token).
    provider: str | None = Field(default=None, max_length=32)
    # "{owner}/{repo}" shorthand, optional.
    provider_repo_name: str | None = Field(default=None, max_length=512)

    # Clone lifecycle: "pending" | "cloning" | "cloned" | "failed"
    clone_status: str = Field(default="pending", max_length=32)
    last_synced_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    error_msg: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # Worker job id that last acted on this repo (for status correlation).
    last_job_id: int | None = Field(default=None, nullable=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
