"""Request and complete password reset using a one-time token (e.g. from email link)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.auth_service.models import PasswordResetToken, Token, UserAuth
from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token


class InvalidResetTokenError(Exception):
    """Unknown, expired, or already used reset token."""


def request_password_reset(session: Session, *, email: str) -> str | None:
    """
    If a user exists for email, invalidate prior pending tokens, create a new one, return raw token.
    If no user, return None (caller must not reveal whether email exists).
    """
    user = session.exec(select(UserAuth).where(UserAuth.email == email)).first()
    if user is None:
        return None

    assert user.user_auth_id is not None
    pending = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.user_auth_id,
            PasswordResetToken.used == False,  # noqa: E712
        )
    ).all()
    for row in pending:
        row.used = True
        session.add(row)

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.password_reset_token_expire_minutes)
    raw = secrets.token_urlsafe(32)
    session.add(
        PasswordResetToken(
            user_id=user.user_auth_id,
            token_hash=hash_refresh_token(raw),
            expires_at=expires_at,
            used=False,
        )
    )
    session.commit()
    return raw


def reset_password_with_token(session: Session, *, token: str, new_password: str) -> None:
    token_hash = hash_refresh_token(token)
    row = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used == False,  # noqa: E712
        )
    ).first()
    now = datetime.now(timezone.utc)
    if row is None or row.expires_at < now:
        raise InvalidResetTokenError

    user = session.get(UserAuth, row.user_id)
    if user is None:
        raise InvalidResetTokenError

    user.password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    session.add(user)
    row.used = True
    session.add(row)

    assert user.user_auth_id is not None
    tokens = session.exec(select(Token).where(Token.user_id == user.user_auth_id)).all()
    for t in tokens:
        if not t.revoked:
            t.revoked = True
            session.add(t)

    session.commit()
    session.refresh(user)
