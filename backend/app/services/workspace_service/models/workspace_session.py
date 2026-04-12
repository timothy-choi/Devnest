"""Per-user workspace access session (opaque token at rest as HMAC-SHA256 hash)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel

from .enums import WorkspaceSessionRole, WorkspaceSessionStatus


class WorkspaceSession(SQLModel, table=True):
    """Grants access coordinates via GET /workspaces/{id}/access when ACTIVE and unexpired.

    Plain session tokens are never stored; see ``workspace_session_service``.
    """

    __tablename__ = "workspace_session"

    workspace_session_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    role: str = Field(
        max_length=32,
        default=WorkspaceSessionRole.OWNER.value,
        index=True,
    )
    status: str = Field(
        max_length=32,
        default=WorkspaceSessionStatus.ACTIVE.value,
        index=True,
    )
    session_token_hash: str = Field(max_length=128, unique=True, index=True)
    issued_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    last_seen_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    client_metadata: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
