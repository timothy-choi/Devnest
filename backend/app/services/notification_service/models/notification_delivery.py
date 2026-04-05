"""Per-recipient per-channel delivery attempt tracking."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from .enums import DeliveryStatus


class NotificationDelivery(SQLModel, table=True):
    __tablename__ = "notification_delivery"

    delivery_id: int | None = Field(default=None, primary_key=True)
    notification_id: int = Field(foreign_key="notification.notification_id", index=True)
    notification_recipient_id: int = Field(foreign_key="notification_recipient.notification_recipient_id", index=True)
    channel: str = Field(max_length=32)
    provider: str = Field(max_length=64, default="stub")
    status: str = Field(max_length=32, default=DeliveryStatus.QUEUED.value, index=True)
    attempt_count: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=2048)
    provider_message_id: str | None = Field(default=None, max_length=512)
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    delivered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
