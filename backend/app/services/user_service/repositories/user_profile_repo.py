"""Persistence for ``UserProfile`` rows."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.services.user_service.models import UserProfile


def get_by_user_id(session: Session, user_id: int) -> UserProfile | None:
    return session.get(UserProfile, user_id)


def get_public_by_user_id(session: Session, user_id: int) -> UserProfile | None:
    """
    Load profile for a user id suitable for public projection.

    The table holds no email; callers map to ``PublicUserProfileResponse`` (or equivalent).
    """
    return get_by_user_id(session, user_id)


def create_profile(
    session: Session,
    *,
    user_id: int,
    display_name: str = "",
    first_name: str | None = None,
    last_name: str | None = None,
    bio: str | None = None,
    avatar_url: str | None = None,
    tz: str | None = None,
    locale: str | None = None,
) -> UserProfile:
    now = datetime.now(timezone.utc)
    row = UserProfile(
        user_id=user_id,
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        bio=bio,
        avatar_url=avatar_url,
        timezone=tz,
        locale=locale,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_profile(session: Session, row: UserProfile) -> UserProfile:
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def upsert_profile_if_missing(session: Session, user_id: int) -> UserProfile:
    existing = get_by_user_id(session, user_id)
    if existing is not None:
        return existing
    return create_profile(session, user_id=user_id)
