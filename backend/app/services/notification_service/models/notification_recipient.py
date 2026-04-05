"""Per-user receipt of a notification."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from .enums import RecipientStatus


class NotificationRecipient(SQLModel, table=True):
    __tablename__ = "notification_recipient"
    __table_args__ = (UniqueConstraint("notification_id", "user_id", name="uq_notification_recipient_notif_user"),)

    notification_recipient_id: int | None = Field(default=None, primary_key=True)
    notification_id: int = Field(foreign_key="notification.notification_id", index=True)
    user_id: int = Field(foreign_key="user_auth.user_auth_id", index=True)
    status: str = Field(max_length=32, default=RecipientStatus.PENDING.value, index=True)
    read_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    dismissed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
