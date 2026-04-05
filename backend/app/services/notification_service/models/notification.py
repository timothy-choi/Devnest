"""Core notification record (logical event)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel

from .enums import NotificationPriority, NotificationStatus


class Notification(SQLModel, table=True):
    __tablename__ = "notification"

    notification_id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True, max_length=128)
    title: str = Field(max_length=512)
    body: str = Field(max_length=8192)
    payload_json: dict | list | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    source_service: str = Field(index=True, max_length=128)
    source_event_id: str | None = Field(default=None, max_length=255, index=True)
    priority: str = Field(max_length=32, default=NotificationPriority.NORMAL.value)
    status: str = Field(max_length=32, default=NotificationStatus.PENDING.value, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
