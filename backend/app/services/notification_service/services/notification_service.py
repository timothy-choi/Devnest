"""Create notifications, list/read/dismiss for users (orchestrates dispatch)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.services.notification_service.models import Notification, NotificationRecipient
from app.services.notification_service.models.enums import NotificationPriority, NotificationStatus, RecipientStatus
from app.services.notification_service.repositories import notification_repo, recipient_repo
from app.services.notification_service.services import dispatch_service
from app.services.notification_service.services.exceptions import NotificationNotFoundError


def create_notification_event(
    session: Session,
    *,
    type: str,
    title: str,
    body: str,
    payload_json: dict[str, Any] | list[Any] | None,
    recipient_user_ids: list[int],
    priority: str,
    source_service: str,
    source_event_id: str | None,
) -> Notification:
    notif = notification_repo.create_notification(
        session,
        type=type,
        title=title,
        body=body,
        payload_json=payload_json,
        source_service=source_service,
        source_event_id=source_event_id,
        priority=priority,
        status=NotificationStatus.PENDING.value,
    )
    assert notif.notification_id is not None
    for uid in recipient_user_ids:
        recipient = recipient_repo.create_recipient(
            session,
            notification_id=notif.notification_id,
            user_id=uid,
            status=RecipientStatus.PENDING.value,
        )
        dispatch_service.dispatch_for_recipient(session, notif, recipient)
    dispatch_service.refresh_notification_status(session, notif.notification_id)
    refreshed = notification_repo.get_notification_by_id(session, notif.notification_id)
    assert refreshed is not None
    return refreshed


def list_notifications_for_user(
    session: Session,
    user_id: int,
    *,
    filter_mode: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[Notification, NotificationRecipient]], int]:
    return recipient_repo.list_notifications_for_user(
        session, user_id, filter_mode=filter_mode, limit=limit, offset=offset
    )


def get_notification_for_user(
    session: Session,
    user_id: int,
    notification_id: int,
) -> tuple[Notification, NotificationRecipient]:
    rec = recipient_repo.get_recipient_by_notification_and_user(
        session, notification_id=notification_id, user_id=user_id
    )
    if rec is None:
        raise NotificationNotFoundError
    notif = notification_repo.get_notification_by_id(session, notification_id)
    if notif is None:
        raise NotificationNotFoundError
    return notif, rec


def mark_read(session: Session, user_id: int, notification_id: int) -> NotificationRecipient:
    notif, rec = get_notification_for_user(session, user_id, notification_id)
    _ = notif
    return recipient_repo.mark_read(session, rec)


def mark_read_bulk(session: Session, user_id: int, notification_ids: list[int]) -> list[NotificationRecipient]:
    rows = recipient_repo.list_recipients_for_user_and_notifications(session, user_id, notification_ids)
    updated: list[NotificationRecipient] = []
    for rec in rows:
        updated.append(recipient_repo.mark_read(session, rec))
    return updated


def dismiss_notification(session: Session, user_id: int, notification_id: int) -> NotificationRecipient:
    notif, rec = get_notification_for_user(session, user_id, notification_id)
    _ = notif
    return recipient_repo.mark_dismissed(session, rec)


def validate_priority(value: str) -> str:
    """Return normalized priority or raise ValueError."""
    try:
        return NotificationPriority(value).value
    except ValueError as e:
        raise ValueError(f"invalid priority: {value}") from e
