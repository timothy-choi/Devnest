"""Queued or in-flight workspace jobs (execution deferred; orchestrator updates outcomes)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, SQLModel

from .enums import WorkspaceJobStatus


class WorkspaceJob(SQLModel, table=True):
    __tablename__ = "workspace_job"

    workspace_job_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    workspace_snapshot_id: int | None = Field(
        default=None,
        foreign_key="workspace_snapshot.workspace_snapshot_id",
        index=True,
        description="Set for SNAPSHOT_CREATE / SNAPSHOT_RESTORE jobs.",
    )
    job_type: str = Field(
        sa_column=Column("type", String(32), nullable=False, index=True),
    )
    status: str = Field(
        max_length=32,
        default=WorkspaceJobStatus.QUEUED.value,
        index=True,
    )
    requested_by_user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    requested_config_version: int = Field(ge=1)
    attempt: int = Field(default=0, ge=0)
    error_msg: str | None = Field(default=None, max_length=8192)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    correlation_id: str | None = Field(
        default=None,
        max_length=64,
        index=True,
        description="Request/job correlation id for logs and gateway propagation (V1 observability).",
    )
    max_attempts: int = Field(
        default=2,
        ge=1,
        description="Worker execution tries (each claim increments attempt); default 2 aligns with design doc.",
    )
    failure_stage: str | None = Field(default=None, max_length=32, index=True)
    failure_code: str | None = Field(default=None, max_length=64)
    next_attempt_after: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
        description="When set, dequeue skips this row until this time (retry backoff).",
    )
