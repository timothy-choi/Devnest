"""Persistence for ``NotificationRecipient`` rows."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.services.notification_service.models import Notification, NotificationRecipient, RecipientStatus


def create_recipient(
    session: Session,
    *,
    notification_id: int,
    user_id: int,
    status: str,
) -> NotificationRecipient:
    row = NotificationRecipient(notification_id=notification_id, user_id=user_id, status=status)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_recipient_by_id(session: Session, notification_recipient_id: int) -> NotificationRecipient | None:
    return session.get(NotificationRecipient, notification_recipient_id)


def get_recipient_by_notification_and_user(
    session: Session,
    *,
    notification_id: int,
    user_id: int,
) -> NotificationRecipient | None:
    return session.exec(
        select(NotificationRecipient).where(
            NotificationRecipient.notification_id == notification_id,
            NotificationRecipient.user_id == user_id,
        )
    ).first()


def list_recipients_for_user_and_notifications(
    session: Session,
    user_id: int,
    notification_ids: list[int],
) -> list[NotificationRecipient]:
    if not notification_ids:
        return []
    return list(
        session.exec(
            select(NotificationRecipient).where(
                NotificationRecipient.user_id == user_id,
                NotificationRecipient.notification_id.in_(notification_ids),
            )
        ).all()
    )


def list_notifications_for_user(
    session: Session,
    user_id: int,
    *,
    filter_mode: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[Notification, NotificationRecipient]], int]:
    """
    filter_mode: ``all`` | ``unread`` | ``read`` (read_at set).
    Returns (page of (Notification, NotificationRecipient), total count for this filter).
    """
    count_stmt = select(func.count()).select_from(NotificationRecipient).where(
        NotificationRecipient.user_id == user_id
    )
    page_stmt = (
        select(Notification, NotificationRecipient)
        .join(NotificationRecipient, NotificationRecipient.notification_id == Notification.notification_id)
        .where(NotificationRecipient.user_id == user_id)
    )

    if filter_mode == "unread":
        extra = (
            NotificationRecipient.read_at.is_(None),
            NotificationRecipient.dismissed_at.is_(None),
        )
        count_stmt = count_stmt.where(*extra)
        page_stmt = page_stmt.where(*extra)
    elif filter_mode == "read":
        count_stmt = count_stmt.where(NotificationRecipient.read_at.is_not(None))
        page_stmt = page_stmt.where(NotificationRecipient.read_at.is_not(None))

    total = session.exec(count_stmt).one()
    page_stmt = page_stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    rows = list(session.exec(page_stmt).all())
    return rows, total


def update_recipient(session: Session, row: NotificationRecipient) -> NotificationRecipient:
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def mark_read(session: Session, row: NotificationRecipient, *, read_at: datetime | None = None) -> NotificationRecipient:
    ts = read_at or datetime.now(timezone.utc)
    row.read_at = ts
    row.status = RecipientStatus.READ.value
    return update_recipient(session, row)


def mark_dismissed(session: Session, row: NotificationRecipient) -> NotificationRecipient:
    row.dismissed_at = datetime.now(timezone.utc)
    row.status = RecipientStatus.DISMISSED.value
    return update_recipient(session, row)
