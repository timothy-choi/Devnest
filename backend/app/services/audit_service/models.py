"""Durable, append-only audit log row."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    """Append-only record of audited platform actions.

    Rows are never updated or deleted through normal application flows.
    TODO: Enforce append-only at the DB layer (row-level security / immutable table policy) in production.
    """

    __tablename__ = "audit_log"

    audit_log_id: int | None = Field(default=None, primary_key=True)
    # ON DELETE SET NULL: deleting a user preserves the audit trail with actor_user_id nulled out.
    actor_user_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("user_auth.user_auth_id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
        description="Authenticated user who triggered the action; None for internal/system actions.",
    )
    actor_type: str = Field(
        max_length=32,
        index=True,
        description="AuditActorType value: user | system | internal_service.",
    )
    action: str = Field(
        max_length=128,
        index=True,
        description="Stable dotted action name, e.g. workspace.start.requested.",
    )
    resource_type: str = Field(
        max_length=64,
        index=True,
        description="Affected resource class, e.g. workspace, snapshot, node, session.",
    )
    resource_id: str | None = Field(
        default=None,
        max_length=255,
        description="String-coerced PK of the affected resource (may be int or UUID).",
    )
    workspace_id: int | None = Field(
        default=None,
        foreign_key="workspace.workspace_id",
        index=True,
    )
    job_id: int | None = Field(
        default=None,
        foreign_key="workspace_job.workspace_job_id",
        index=True,
    )
    node_id: str | None = Field(default=None, max_length=255)
    outcome: str = Field(
        max_length=32,
        index=True,
        description="AuditOutcome value: success | failure | denied.",
    )
    reason: str | None = Field(default=None, max_length=4096)
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    correlation_id: str | None = Field(default=None, max_length=64, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        Index("ix_audit_log_workspace_id_created_at", "workspace_id", "created_at"),
        Index("ix_audit_log_actor_user_id_created_at", "actor_user_id", "created_at"),
    )
