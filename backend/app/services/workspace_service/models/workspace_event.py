"""Append-only control-plane events for workspace lifecycle (SSE / observability)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel


class WorkspaceEvent(SQLModel, table=True):
    __tablename__ = "workspace_event"

    workspace_event_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    event_type: str = Field(max_length=64, index=True)
    status: str | None = Field(default=None, max_length=32)
    message: str | None = Field(default=None, max_length=1024)
    payload_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
