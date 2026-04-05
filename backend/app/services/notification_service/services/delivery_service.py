"""Execute and retry per-channel deliveries."""

from __future__ import annotations

from sqlmodel import Session

from app.services.notification_service.channels import email_channel, in_app_channel, push_channel
from app.services.notification_service.models import NotificationDelivery
from app.services.notification_service.models.enums import DeliveryChannel, DeliveryStatus, RecipientStatus
from app.services.notification_service.repositories import delivery_repo, notification_repo, recipient_repo
from app.services.notification_service.services.exceptions import DeliveryNotFoundError, InvalidDeliveryStateError


def try_deliver(session: Session, delivery_id: int) -> NotificationDelivery | None:
    """
    Run the appropriate channel for a queued delivery (or no-op for SKIPPED).
    On success (DELIVERED), promotes recipient PENDING -> DELIVERED when applicable.
    """
    d = delivery_repo.get_delivery_by_id(session, delivery_id)
    if d is None:
        return None
    if d.status == DeliveryStatus.SKIPPED.value:
        return d

    d.attempt_count += 1
    delivery_repo.update_delivery(session, d)

    notification = notification_repo.get_notification_by_id(session, d.notification_id)
    recipient = recipient_repo.get_recipient_by_id(session, d.notification_recipient_id)
    if notification is None or recipient is None:
        d.status = DeliveryStatus.FAILED.value
        d.last_error_message = "missing_notification_or_recipient"
        delivery_repo.update_delivery(session, d)
        return delivery_repo.get_delivery_by_id(session, delivery_id)

    try:
        if d.channel == DeliveryChannel.IN_APP.value:
            in_app_channel.apply_in_app_delivery(session, d, notification, recipient)
        elif d.channel == DeliveryChannel.EMAIL.value:
            email_channel.apply_email_delivery(session, d, notification, recipient)
        elif d.channel == DeliveryChannel.PUSH.value:
            push_channel.apply_push_delivery(session, d, notification, recipient)
        else:
            d.status = DeliveryStatus.FAILED.value
            d.last_error_message = f"unknown_channel:{d.channel}"
            delivery_repo.update_delivery(session, d)
    except Exception as e:  # noqa: BLE001 — surface as failed delivery
        fresh = delivery_repo.get_delivery_by_id(session, delivery_id)
        if fresh is not None:
            fresh.status = DeliveryStatus.FAILED.value
            fresh.last_error_message = str(e)[:2048]
            delivery_repo.update_delivery(session, fresh)

    d_final = delivery_repo.get_delivery_by_id(session, delivery_id)
    if d_final is None:
        return None
    rec = recipient_repo.get_recipient_by_id(session, d_final.notification_recipient_id)
    if (
        d_final.status == DeliveryStatus.DELIVERED.value
        and rec is not None
        and rec.status == RecipientStatus.PENDING.value
    ):
        rec.status = RecipientStatus.DELIVERED.value
        recipient_repo.update_recipient(session, rec)
    return d_final


def retry_delivery(session: Session, delivery_id: int) -> NotificationDelivery:
    d = delivery_repo.get_delivery_by_id(session, delivery_id)
    if d is None:
        raise DeliveryNotFoundError
    if d.status != DeliveryStatus.FAILED.value:
        raise InvalidDeliveryStateError
    d.status = DeliveryStatus.QUEUED.value
    d.last_error_code = None
    d.last_error_message = None
    delivery_repo.update_delivery(session, d)
    result = try_deliver(session, delivery_id)
    if result is None:
        raise DeliveryNotFoundError
    return result
