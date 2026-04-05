"""Request bodies for notification APIs (user + internal)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class InternalCreateNotificationRequest(BaseModel):
    type: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=8192)
    payload: dict[str, Any] | list[Any] | None = None
    recipient_user_ids: list[int] = Field(min_length=1)
    priority: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] = "NORMAL"
    source_service: str = Field(min_length=1, max_length=128)
    source_event_id: str | None = Field(default=None, max_length=255)


class NotificationReadBulkRequest(BaseModel):
    notification_ids: list[int] = Field(min_length=1, max_length=500)
