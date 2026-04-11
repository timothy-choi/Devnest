"""Runtime placement and health (orchestrator-owned rows; not created on workspace intent)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from .enums import WorkspaceRuntimeHealthStatus


class WorkspaceRuntime(SQLModel, table=True):
    __tablename__ = "workspace_runtime"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_workspace_runtime_workspace"),)

    workspace_runtime_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    node_id: str | None = Field(default=None, max_length=255, index=True)
    container_id: str | None = Field(default=None, max_length=255)
    container_state: str | None = Field(default=None, max_length=64)
    topology_id: int | None = Field(default=None, index=True)
    internal_endpoint: str | None = Field(default=None, max_length=512)
    config_version: int | None = Field(default=None, ge=1)
    health_status: str = Field(
        default=WorkspaceRuntimeHealthStatus.UNKNOWN.value,
        max_length=32,
        index=True,
    )
    last_heartbeat_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
