"""OAuth provider linkage (SQLModel / FastAPI stack)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class OAuth(SQLModel, table=True):
    __tablename__ = "oauth"
    __table_args__ = (UniqueConstraint("oauth_provider", "provider_user_id", name="uq_oauth_provider_user_id"),)

    oauth_id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    oauth_provider: str = Field(index=True, max_length=64)
    provider_user_id: str = Field(index=True, max_length=255)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
