"""Workspace-scoped CI/CD configuration.

V1 supports GitHub Actions via `repository_dispatch` events. Each workspace
has at most one CI configuration (1:1 unique constraint on workspace_id).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class WorkspaceCIConfig(SQLModel, table=True):
    __tablename__ = "workspace_ci_config"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_ci_config"),
    )

    ci_config_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    owner_user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)

    # Provider: "github_actions" (only supported value in V1)
    provider: str = Field(default="github_actions", max_length=64)

    # GitHub repository owner and name (separate fields for API calls).
    repo_owner: str = Field(max_length=255)
    repo_name: str = Field(max_length=255)

    # Workflow file name (e.g. "ci.yml"). Used for display only; dispatch uses event_type.
    workflow_file: str = Field(default="main.yml", max_length=255)
    # Default branch / ref for dispatched workflows.
    default_branch: str = Field(default="main", max_length=255)

    is_active: bool = Field(default=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
