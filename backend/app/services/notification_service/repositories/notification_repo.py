"""Persistence for ``Notification`` rows."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.services.notification_service.models import Notification


def create_notification(
    session: Session,
    *,
    type: str,
    title: str,
    body: str,
    payload_json: dict[str, Any] | list[Any] | None,
    source_service: str,
    source_event_id: str | None,
    priority: str,
    status: str,
) -> Notification:
    row = Notification(
        type=type,
        title=title,
        body=body,
        payload_json=payload_json,
        source_service=source_service,
        source_event_id=source_event_id,
        priority=priority,
        status=status,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_notification_by_id(session: Session, notification_id: int) -> Notification | None:
    return session.get(Notification, notification_id)


def update_notification(session: Session, row: Notification) -> Notification:
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
