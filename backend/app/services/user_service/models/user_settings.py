"""Per-user UI / default notification toggles (no workspace fields)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime
from sqlmodel import Field, SQLModel


class UserSettings(SQLModel, table=True):
    __tablename__ = "user_settings"

    user_id: int = Field(foreign_key="user_auth.user_auth_id", primary_key=True)
    theme: str | None = Field(default=None, max_length=64)
    default_notification_email_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    default_notification_push_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    default_notification_in_app_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
