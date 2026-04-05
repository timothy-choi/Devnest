"""Persistence for ``NotificationPreference`` rows."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.services.notification_service.models import NotificationPreference


def list_preferences_for_user(session: Session, user_id: int) -> list[NotificationPreference]:
    return list(
        session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).all()
    )


def get_preference_by_user_and_type(
    session: Session,
    user_id: int,
    notification_type: str,
) -> NotificationPreference | None:
    return session.exec(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.notification_type == notification_type,
        )
    ).first()


def upsert_preference(
    session: Session,
    *,
    user_id: int,
    notification_type: str,
    in_app_enabled: bool,
    email_enabled: bool,
    push_enabled: bool,
) -> NotificationPreference:
    now = datetime.now(timezone.utc)
    existing = get_preference_by_user_and_type(session, user_id, notification_type)
    if existing is None:
        row = NotificationPreference(
            user_id=user_id,
            notification_type=notification_type,
            in_app_enabled=in_app_enabled,
            email_enabled=email_enabled,
            push_enabled=push_enabled,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        existing.in_app_enabled = in_app_enabled
        existing.email_enabled = email_enabled
        existing.push_enabled = push_enabled
        existing.updated_at = now
        session.add(existing)
        row = existing
    session.commit()
    session.refresh(row)
    return row


def update_preference_row(session: Session, row: NotificationPreference) -> NotificationPreference:
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
