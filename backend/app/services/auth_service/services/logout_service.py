"""Logout: hash refresh token, mark matching DB row revoked."""

from sqlmodel import Session, select

from app.services.auth_service.models import Token
from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token


class UnknownRefreshTokenError(Exception):
    """No active refresh token row for the given value."""


def logout_refresh_token(session: Session, *, refresh_token: str) -> None:
    token_hash = hash_refresh_token(refresh_token)
    row = session.exec(select(Token).where(Token.token_hash == token_hash)).first()
    if row is None or row.revoked:
        raise UnknownRefreshTokenError
    row.revoked = True
    session.add(row)
    session.commit()
