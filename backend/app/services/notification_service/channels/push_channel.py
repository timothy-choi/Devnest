"""Push channel: FCM / Web Push (provider integration stubbed)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.services.notification_service.models import Notification, NotificationDelivery, NotificationRecipient
from app.services.notification_service.models.enums import DeliveryStatus
from app.services.notification_service.repositories import delivery_repo, push_subscription_repo


def send_push(
    session: Session,
    *,
    user_id: int,
    notification: Notification,
    delivery: NotificationDelivery,
) -> tuple[bool, str | None, str | None]:
    """
    Returns (success, provider_message_id, error_message).
    Stub: succeeds if the user has at least one active subscription; otherwise fails.
    TODO: integrate web-push (VAPID) or FCM with ``endpoint`` / ``device_token``.
    """
    subs = push_subscription_repo.list_subscriptions_for_user(session, user_id, include_revoked=False)
    if not subs:
        return False, None, "no_active_push_subscriptions"
    _ = notification
    _ = delivery
    return True, "stub-push-message-id", None


def apply_push_delivery(
    session: Session,
    delivery: NotificationDelivery,
    notification: Notification,
    recipient: NotificationRecipient,
) -> None:
    ok, mid, err = send_push(
        session,
        user_id=recipient.user_id,
        notification=notification,
        delivery=delivery,
    )
    now = datetime.now(timezone.utc)
    if ok:
        delivery.status = DeliveryStatus.DELIVERED.value
        delivery.sent_at = now
        delivery.delivered_at = now
        delivery.provider_message_id = mid
        delivery.provider = "stub_push"
    else:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.last_error_message = err
    delivery_repo.update_delivery(session, delivery)
