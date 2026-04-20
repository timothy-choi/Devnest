"""Encrypted per-workspace secrets injected only at runtime."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class WorkspaceSecret(SQLModel, table=True):
    __tablename__ = "workspace_secret"
    __table_args__ = (
        UniqueConstraint("workspace_id", "secret_name", name="uq_workspace_secret_name"),
    )

    workspace_secret_id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.workspace_id", index=True)
    secret_name: str = Field(max_length=128, index=True)
    encrypted_value: str = Field(max_length=8192)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
