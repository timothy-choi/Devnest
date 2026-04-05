"""In-app channel: notification is available via DB (delivery row marks completion)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.services.notification_service.models import Notification, NotificationDelivery, NotificationRecipient
from app.services.notification_service.models.enums import DeliveryStatus
from app.services.notification_service.repositories import delivery_repo


def apply_in_app_delivery(
    session: Session,
    delivery: NotificationDelivery,
    notification: Notification,
    recipient: NotificationRecipient,
) -> None:
    """Mark delivery as delivered in-app (no external provider)."""
    _ = notification
    _ = recipient
    now = datetime.now(timezone.utc)
    delivery.status = DeliveryStatus.DELIVERED.value
    delivery.sent_at = now
    delivery.delivered_at = now
    delivery.provider = "in_app"
    delivery_repo.update_delivery(session, delivery)
