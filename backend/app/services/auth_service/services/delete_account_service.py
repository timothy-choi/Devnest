"""Delete a user account and dependent rows (tokens, profile, notifications, etc.)."""

from __future__ import annotations

import bcrypt
from sqlmodel import Session, select

from app.services.auth_service.models import OAuth, PasswordResetToken, Token, UserAuth
from app.services.notification_service.models import NotificationDelivery, NotificationPreference, NotificationRecipient, PushSubscription
from app.services.user_service.models import UserProfile, UserSettings


class InvalidAccountPasswordError(Exception):
    """Password required or incorrect for account deletion."""


def _user_has_oauth_link(session: Session, user_id: int) -> bool:
    return (
        session.exec(select(OAuth).where(OAuth.user_id == user_id)).first() is not None
    )


def delete_account_for_current_user(
    session: Session,
    user: UserAuth,
    *,
    password: str | None,
) -> None:
    """
    Remove the user and related data.

    Local (password) accounts must supply the current password. Accounts with at least
    one OAuth link may omit the password (caller is already authenticated).
    """
    uid = user.user_auth_id
    if uid is None:
        raise InvalidAccountPasswordError

    has_oauth = _user_has_oauth_link(session, uid)
    if not has_oauth:
        if not password:
            raise InvalidAccountPasswordError
        if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
            raise InvalidAccountPasswordError

    _purge_user_related_rows(session, uid)

    row = session.get(UserAuth, uid)
    if row is not None:
        session.delete(row)
    session.commit()


def _purge_user_related_rows(session: Session, user_id: int) -> None:
    recipients = list(
        session.exec(select(NotificationRecipient).where(NotificationRecipient.user_id == user_id)).all()
    )
    for r in recipients:
        rid = r.notification_recipient_id
        if rid is not None:
            for d in session.exec(
                select(NotificationDelivery).where(NotificationDelivery.notification_recipient_id == rid)
            ).all():
                session.delete(d)
        session.delete(r)

    for p in session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).all():
        session.delete(p)
    for s in session.exec(select(PushSubscription).where(PushSubscription.user_id == user_id)).all():
        session.delete(s)
    for t in session.exec(select(Token).where(Token.user_id == user_id)).all():
        session.delete(t)
    for o in session.exec(select(OAuth).where(OAuth.user_id == user_id)).all():
        session.delete(o)
    for pr in session.exec(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)).all():
        session.delete(pr)

    prof = session.get(UserProfile, user_id)
    if prof is not None:
        session.delete(prof)
    settings = session.get(UserSettings, user_id)
    if settings is not None:
        session.delete(settings)
