"""Policy data model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class Policy(SQLModel, table=True):
    """Platform governance policy.

    ``rules_json`` stores a dict of operational rules (see service.py for the supported keys).
    Policies are evaluated in order of creation (oldest first) and the first denial wins.

    Supported rule keys (all optional; absent = no restriction):
      allow_workspace_creation (bool)  — default True
      allow_workspace_start    (bool)  — default True
      allow_snapshot_creation  (bool)  — default True
      allow_session_creation   (bool)  — default True
      allow_node_provisioning  (bool)  — default True
      allowed_runtime_images   (list[str] | null) — null means unrestricted
      require_private_workspaces (bool) — default False

    TODO: add ABAC / CEL expression support when a policy DSL is needed.
    TODO: add org-scoped policies when multi-tenancy is introduced.
    """

    __tablename__ = "policy"

    policy_id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=128, unique=True, index=True)
    description: str | None = Field(default=None, max_length=1024)
    policy_type: str = Field(
        max_length=32,
        index=True,
        description="PolicyType: system | user | workspace",
    )
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
    rules_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default="{}"),
    )
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        Index("ix_policy_scope_type_scope_id", "scope_type", "scope_id"),
        Index("ix_policy_is_active_scope", "is_active", "scope_type"),
    )
