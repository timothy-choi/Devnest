"""Web / mobile push subscription endpoints (keys vary by platform)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime
from sqlmodel import Field, SQLModel


class PushSubscription(SQLModel, table=True):
    __tablename__ = "push_subscription"

    push_subscription_id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    platform: str = Field(max_length=16, index=True)
    endpoint: str = Field(max_length=2048, index=True)
    p256dh_key: str | None = Field(default=None, max_length=512)
    auth_key: str | None = Field(default=None, max_length=256)
    device_token: str | None = Field(default=None, max_length=512)
    device_name: str | None = Field(default=None, max_length=255)
    last_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    revoked: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
