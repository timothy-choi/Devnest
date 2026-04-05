"""Email channel: render + send via SMTP when configured, else stub."""

from __future__ import annotations

import secrets
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.auth_service.models import UserAuth
from app.services.notification_service.models import Notification, NotificationDelivery, NotificationRecipient
from app.services.notification_service.models.enums import DeliveryStatus
from app.services.notification_service.repositories import delivery_repo


def render_email(*, notification: Notification, to_email: str) -> dict[str, Any]:
    """
    Build a minimal MIME-friendly payload. Replace with templates (Jinja) when needed.
    """
    return {
        "to": to_email,
        "subject": notification.title,
        "text_body": notification.body,
        "html_body": f"<html><body><p>{notification.body}</p></body></html>",
    }


def _build_mime_message(*, rendered: dict[str, Any], from_address: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = rendered["subject"]
    msg["From"] = from_address
    msg["To"] = rendered["to"]
    msg.attach(MIMEText(rendered["text_body"], "plain", "utf-8"))
    msg.attach(MIMEText(rendered["html_body"], "html", "utf-8"))
    return msg


def _send_via_smtp(*, rendered: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    settings = get_settings()
    host = settings.smtp_host.strip()

    from_addr = settings.smtp_from_address.strip()
    if not from_addr:
        return False, None, "smtp_from_address is not configured"

    to_addr = (rendered.get("to") or "").strip()
    if not to_addr:
        return False, None, "recipient address is empty"

    msg = _build_mime_message(rendered=rendered, from_address=from_addr)

    try:
        with smtplib.SMTP(host, settings.smtp_port, timeout=30) as server:
            if settings.smtp_use_tls:
                server.starttls()
            user = settings.smtp_user.strip()
            if user:
                server.login(user, settings.smtp_password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        return False, None, str(e)[:2048]

    mid = f"smtp-{secrets.token_hex(8)}"
    return True, mid, None


def send_email(*, rendered: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """
    Returns (success, provider_message_id, error_message).
    Uses SMTP when ``smtp_host`` is set in settings; otherwise stub success (no network).
    """
    settings = get_settings()
    if not settings.smtp_host.strip():
        _ = rendered
        return True, "stub-email-message-id", None
    return _send_via_smtp(rendered=rendered)


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
    settings = get_settings()
    provider = "smtp" if settings.smtp_host.strip() else "stub_email"
    if ok:
        delivery.status = DeliveryStatus.DELIVERED.value
        delivery.sent_at = now
        delivery.delivered_at = now
        delivery.provider_message_id = mid
        delivery.provider = provider
    else:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.last_error_message = err
    delivery_repo.update_delivery(session, delivery)
