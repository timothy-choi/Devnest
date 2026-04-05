"""Notification list/detail API responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotificationItemResponse(BaseModel):
    """Single row for the authenticated user: notification + receipt state."""

    model_config = ConfigDict(from_attributes=True)

    notification_id: int
    notification_recipient_id: int
    type: str
    title: str
    body: str
    payload: dict[str, Any] | list[Any] | None = None
    priority: str
    source_service: str
    source_event_id: str | None
    recipient_status: str
    read_at: datetime | None
    dismissed_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItemResponse]
    total: int


class NotificationDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: int
    notification_recipient_id: int
    type: str
    title: str
    body: str
    payload: dict[str, Any] | list[Any] | None = None
    priority: str
    status: str
    source_service: str
    source_event_id: str | None
    recipient_status: str
    read_at: datetime | None
    dismissed_at: datetime | None
    created_at: datetime


class InternalCreateNotificationResponse(BaseModel):
    notification_id: int
    status: str


class DeliveryRetryResponse(BaseModel):
    delivery_id: int
    status: str
    attempt_count: int
    last_error_message: str | None = None
