"""Login: validate credentials, persist hashed refresh token, return access + refresh tokens."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.auth_service.models import Token, UserAuth
from app.services.auth_service.services.auth_token import create_access_token
from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token, new_refresh_token_value


class InvalidCredentialsError(Exception):
    """Unknown user or wrong password (do not distinguish for clients)."""


@dataclass(frozen=True)
class LoginTokens:
    access_token: str
    refresh_token: str


def login_user(session: Session, *, username: str, password: str) -> LoginTokens:
    user = session.exec(select(UserAuth).where(UserAuth.username == username)).first()
    if user is None:
        raise InvalidCredentialsError
    if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        raise InvalidCredentialsError
    assert user.user_auth_id is not None

    refresh_plain = new_refresh_token_value()
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)

    token_row = Token(
        user_id=user.user_auth_id,
        token_hash=hash_refresh_token(refresh_plain),
        expires_at=expires_at,
        revoked=False,
    )
    session.add(token_row)
    session.commit()
    session.refresh(token_row)

    access = create_access_token(user_id=user.user_auth_id)
    return LoginTokens(access_token=access, refresh_token=refresh_plain)
