"""Durable cleanup tasks: idempotent retries until runtime/topology state matches reality."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from .enums import WorkspaceCleanupTaskStatus


class WorkspaceCleanupTask(SQLModel, table=True):
    """One row per (workspace, scope); retried by reconcile until ``SUCCEEDED``."""

    __tablename__ = "workspace_cleanup_task"
    __table_args__ = (
        UniqueConstraint("workspace_id", "scope", name="uq_workspace_cleanup_workspace_scope"),
    )

    cleanup_task_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    scope: str = Field(max_length=64, index=True)
    detail: str | None = Field(default=None, max_length=8192)
    status: str = Field(
        default=WorkspaceCleanupTaskStatus.PENDING.value,
        max_length=32,
        index=True,
    )
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
