"""Persistence for ``NotificationDelivery`` rows."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.services.notification_service.models import NotificationDelivery


def create_delivery(
    session: Session,
    *,
    notification_id: int,
    notification_recipient_id: int,
    channel: str,
    provider: str = "stub",
    status: str,
    attempt_count: int = 0,
) -> NotificationDelivery:
    now = datetime.now(timezone.utc)
    row = NotificationDelivery(
        notification_id=notification_id,
        notification_recipient_id=notification_recipient_id,
        channel=channel,
        provider=provider,
        status=status,
        attempt_count=attempt_count,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_delivery_by_id(session: Session, delivery_id: int) -> NotificationDelivery | None:
    return session.get(NotificationDelivery, delivery_id)


def list_deliveries_for_recipient(session: Session, notification_recipient_id: int) -> list[NotificationDelivery]:
    return list(
        session.exec(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_recipient_id == notification_recipient_id
            )
        ).all()
    )


def list_deliveries_for_notification(session: Session, notification_id: int) -> list[NotificationDelivery]:
    return list(
        session.exec(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == notification_id)
        ).all()
    )


def update_delivery(session: Session, row: NotificationDelivery) -> NotificationDelivery:
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
