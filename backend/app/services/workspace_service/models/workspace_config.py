"""Versioned workspace intent configuration (JSON); workers/orchestrator consume later."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


class WorkspaceConfig(SQLModel, table=True):
    __tablename__ = "workspace_config"
    __table_args__ = (UniqueConstraint("workspace_id", "version", name="uq_workspace_config_version"),)

    workspace_config_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    version: int = Field(ge=1, index=True)
    config_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
