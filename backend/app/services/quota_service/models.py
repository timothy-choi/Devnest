"""Quota data model."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel


class Quota(SQLModel, table=True):
    """Resource quota definition.

    A Quota row defines limits for a specific scope (GLOBAL, USER, WORKSPACE).
    ``scope_id`` is the user_id or workspace_id depending on ``scope_type``; NULL
    for GLOBAL quotas.

    Limit fields are nullable — ``None`` means unlimited for that dimension.
    Precedence when multiple quotas apply: WORKSPACE > USER > GLOBAL (most specific wins).

    TODO: add ``DailyUsageAggregate`` rollups for efficient max_runtime_hours enforcement.
    TODO: integrate with billing tiers when commercial features are added.
    """

    __tablename__ = "quota"

    quota_id: int | None = Field(default=None, primary_key=True)
    scope_type: str = Field(
        max_length=32,
        index=True,
        description="ScopeType: global | user | workspace",
    )
    scope_id: int | None = Field(
        default=None,
        index=True,
        description="FK to user or workspace depending on scope_type; NULL for global.",
    )
    max_workspaces: int | None = Field(default=None, ge=0)
    max_running_workspaces: int | None = Field(default=None, ge=0)
    max_cpu: float | None = Field(default=None, ge=0)
    max_memory_mb: int | None = Field(default=None, ge=0)
    max_storage_mb: int | None = Field(default=None, ge=0)
    max_sessions: int | None = Field(default=None, ge=0)
    max_snapshots: int | None = Field(default=None, ge=0)
    max_runtime_hours: float | None = Field(default=None, ge=0)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        Index("ix_quota_scope_type_scope_id", "scope_type", "scope_id"),
    )
