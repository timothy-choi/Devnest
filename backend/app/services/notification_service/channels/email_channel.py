"""Email channel: render + send (provider integration stubbed)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.notification_service.models import Notification, NotificationDelivery, NotificationRecipient
from app.services.notification_service.models.enums import DeliveryStatus
from app.services.notification_service.repositories import delivery_repo


def render_email(*, notification: Notification, to_email: str) -> dict[str, Any]:
    """
    Build a minimal MIME-friendly payload. Replace with templates (Jinja) when needed.
    TODO: plug in SMTP / SES / SendGrid in ``send_email``.
    """
    return {
        "to": to_email,
        "subject": notification.title,
        "text_body": notification.body,
        "html_body": f"<html><body><p>{notification.body}</p></body></html>",
    }


def send_email(*, rendered: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """
    Returns (success, provider_message_id, error_message).
    Stub: always succeeds with a fake id.
    """
    _ = rendered
    return True, "stub-email-message-id", None


def apply_email_delivery(
    session: Session,
    delivery: NotificationDelivery,
    notification: Notification,
    recipient: NotificationRecipient,
) -> None:
    user = session.get(UserAuth, recipient.user_id)
    to_email = user.email if user else ""
    rendered = render_email(notification=notification, to_email=to_email)
    ok, mid, err = send_email(rendered=rendered)
    now = datetime.now(timezone.utc)
    if ok:
        delivery.status = DeliveryStatus.DELIVERED.value
        delivery.sent_at = now
        delivery.delivered_at = now
        delivery.provider_message_id = mid
        delivery.provider = "stub_email"
    else:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.last_error_message = err
    delivery_repo.update_delivery(session, delivery)
