"""Usage tracking data models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class WorkspaceUsageRecord(SQLModel, table=True):
    """One usage event for a workspace.

    This is intentionally a simple append-only ledger, not a billing system.
    Aggregates (total runtime seconds, session count, snapshot count) can be
    computed with GROUP BY queries over this table or via a scheduled rollup job.

    TODO: Add a ``DailyUsageAggregate`` table with a scheduled rollup for efficient dashboard queries.
    TODO: Wire ``quantity`` for storage usage (snapshot size_bytes), CPU hours, etc.
    """

    __tablename__ = "workspace_usage_record"

    usage_record_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int | None = Field(
        default=None,
        foreign_key="workspace.workspace_id",
        index=True,
        description="None for platform-level events (e.g. node provisioning) with no workspace scope.",
    )
    owner_user_id: int | None = Field(
        default=None,
        foreign_key="user_auth.user_auth_id",
        index=True,
        description="Workspace owner at time of event. None for internal/system events.",
    )
    event_type: str = Field(
        max_length=64,
        index=True,
        description="UsageEventType value.",
    )
    quantity: int = Field(
        default=1,
        ge=0,
        description="Event magnitude (seconds for runtime events, bytes for storage, count otherwise).",
    )
    node_id: str | None = Field(default=None, max_length=255)
    job_id: int | None = Field(
        default=None,
        foreign_key="workspace_job.workspace_job_id",
        index=True,
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    correlation_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        Index("ix_usage_workspace_event_created", "workspace_id", "event_type", "created_at"),
        Index("ix_usage_owner_event_created", "owner_user_id", "event_type", "created_at"),
    )
