"""User notification preferences (per type, per channel)."""

from __future__ import annotations

from sqlmodel import Session

from app.services.notification_service.models import NotificationPreference
from app.services.notification_service.repositories import preference_repo


def get_preferences(session: Session, user_id: int) -> list[NotificationPreference]:
    return preference_repo.list_preferences_for_user(session, user_id)


def upsert_preference(
    session: Session,
    *,
    user_id: int,
    notification_type: str,
    in_app_enabled: bool,
    email_enabled: bool,
    push_enabled: bool,
) -> NotificationPreference:
    return preference_repo.upsert_preference(
        session,
        user_id=user_id,
        notification_type=notification_type,
        in_app_enabled=in_app_enabled,
        email_enabled=email_enabled,
        push_enabled=push_enabled,
    )
