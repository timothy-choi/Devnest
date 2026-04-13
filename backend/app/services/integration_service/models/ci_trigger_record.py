"""Append-only record of a CI/CD trigger invocation for a workspace."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Text
from sqlmodel import JSON, Field, SQLModel


class CITriggerRecord(SQLModel, table=True):
    __tablename__ = "ci_trigger_record"

    trigger_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    owner_user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)

    provider: str = Field(default="github_actions", max_length=64)
    # GitHub repository_dispatch event_type string.
    event_type: str = Field(default="devnest_trigger", max_length=128)
    # Optional ref override (branch or tag).
    ref: str | None = Field(default=None, max_length=255)
    # Optional caller-supplied input payload stored as JSON.
    inputs_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    triggered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    # "triggered" | "failed"
    status: str = Field(default="triggered", max_length=32)
    error_msg: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # URL to the triggered run, if returned by the provider (GitHub does not return run URLs
    # from repository_dispatch — left for future webhook-driven enrichment).
    provider_run_url: str | None = Field(default=None, max_length=1024)
