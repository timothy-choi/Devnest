"""Workspace aggregate root (control-plane metadata and transactional status)."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from .enums import WorkspaceStatus


class Workspace(SQLModel, table=True):
    __tablename__ = "workspace"

    workspace_id: int | None = Field(default=None, primary_key=True)
    project_storage_key: str | None = Field(
        default_factory=lambda: uuid4().hex,
        index=True,
        max_length=64,
    )
    name: str = Field(index=True, max_length=255)
    description: str | None = Field(default=None, max_length=8192)
    owner_user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    status: str = Field(
        max_length=32,
        default=WorkspaceStatus.CREATING.value,
        index=True,
    )
    status_reason: str | None = Field(default=None, max_length=1024)
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=4096)
    endpoint_ref: str | None = Field(default=None, max_length=512)
    public_host: str | None = Field(default=None, max_length=512)
    active_sessions_count: int = Field(default=0, ge=0)
    is_private: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_started: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_stopped: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
