"""Exchange a refresh token (hashed lookup) for a new access JWT."""

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.services.auth_service.models import Token
from app.services.auth_service.services.auth_token import create_access_token
from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token


class InvalidRefreshTokenError(Exception):
    """Unknown, revoked, or expired refresh token."""


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def refresh_access_token(session: Session, *, refresh_token_plain: str) -> str:
    token_hash = hash_refresh_token(refresh_token_plain)
    row = session.exec(select(Token).where(Token.token_hash == token_hash)).first()
    if row is None or row.revoked:
        raise InvalidRefreshTokenError
    if _aware_utc(row.expires_at) < datetime.now(timezone.utc):
        raise InvalidRefreshTokenError
    return create_access_token(user_id=row.user_id)
