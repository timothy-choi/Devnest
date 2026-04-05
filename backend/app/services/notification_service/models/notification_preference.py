"""Per-user notification channel toggles per notification type."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class NotificationPreference(SQLModel, table=True):
    __tablename__ = "notification_preference"
    __table_args__ = (UniqueConstraint("user_id", "notification_type", name="uq_notification_pref_user_type"),)

    preference_id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    notification_type: str = Field(index=True, max_length=128)
    in_app_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    email_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    push_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
