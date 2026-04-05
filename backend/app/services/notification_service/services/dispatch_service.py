"""Resolve user preferences and create deliveries; aggregate parent notification status."""

from __future__ import annotations

from sqlmodel import Session

from app.services.notification_service.models import Notification, NotificationRecipient
from app.services.notification_service.models.enums import DeliveryChannel, DeliveryStatus, NotificationStatus
from app.services.notification_service.repositories import delivery_repo, notification_repo, preference_repo
from app.services.notification_service.services import delivery_service


def resolve_enabled_channels(session: Session, user_id: int, notification_type: str) -> set[DeliveryChannel]:
    """
    Per-type preference row if present; otherwise all channels enabled.
    """
    pref = preference_repo.get_preference_by_user_and_type(session, user_id, notification_type)
    if pref is None:
        return {DeliveryChannel.IN_APP, DeliveryChannel.EMAIL, DeliveryChannel.PUSH}
    enabled: set[DeliveryChannel] = set()
    if pref.in_app_enabled:
        enabled.add(DeliveryChannel.IN_APP)
    if pref.email_enabled:
        enabled.add(DeliveryChannel.EMAIL)
    if pref.push_enabled:
        enabled.add(DeliveryChannel.PUSH)
    return enabled


def dispatch_for_recipient(
    session: Session,
    notification: Notification,
    recipient: NotificationRecipient,
) -> None:
    assert notification.notification_id is not None
    assert recipient.notification_recipient_id is not None
    enabled = resolve_enabled_channels(session, recipient.user_id, notification.type)

    for channel in (DeliveryChannel.IN_APP, DeliveryChannel.EMAIL, DeliveryChannel.PUSH):
        if channel not in enabled:
            delivery_repo.create_delivery(
                session,
                notification_id=notification.notification_id,
                notification_recipient_id=recipient.notification_recipient_id,
                channel=channel.value,
                status=DeliveryStatus.SKIPPED.value,
            )
        else:
            d = delivery_repo.create_delivery(
                session,
                notification_id=notification.notification_id,
                notification_recipient_id=recipient.notification_recipient_id,
                channel=channel.value,
                status=DeliveryStatus.QUEUED.value,
            )
            assert d.delivery_id is not None
            delivery_service.try_deliver(session, d.delivery_id)


def refresh_notification_status(session: Session, notification_id: int) -> None:
    """Recompute ``Notification.status`` from all delivery rows for this notification."""
    deliveries = delivery_repo.list_deliveries_for_notification(session, notification_id)
    if not deliveries:
        return
    statuses = [d.status for d in deliveries]
    new_status = _derive_notification_status(statuses)
    notif = notification_repo.get_notification_by_id(session, notification_id)
    if notif is None:
        return
    if notif.status != new_status:
        notif.status = new_status
        notification_repo.update_notification(session, notif)


def _derive_notification_status(statuses: list[str]) -> str:
    if any(s == DeliveryStatus.QUEUED.value for s in statuses):
        return NotificationStatus.PENDING.value
    if all(s == DeliveryStatus.SKIPPED.value for s in statuses):
        return NotificationStatus.SENT.value
    if any(s == DeliveryStatus.FAILED.value for s in statuses):
        if any(s == DeliveryStatus.DELIVERED.value for s in statuses):
            return NotificationStatus.PARTIALLY_SENT.value
        return NotificationStatus.FAILED.value
    return NotificationStatus.SENT.value
