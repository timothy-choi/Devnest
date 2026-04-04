"""Refresh / session token storage (SQLModel / FastAPI stack)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime
from sqlmodel import Field, SQLModel


class Token(SQLModel, table=True):
    __tablename__ = "token"

    token_id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    token_hash: str = Field(index=True, max_length=255)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
