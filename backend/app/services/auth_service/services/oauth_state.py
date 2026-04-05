"""Signed short-lived state for OAuth CSRF protection (JWT)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.libs.common.config import get_settings


class OAuthStateError(Exception):
    """Invalid or expired OAuth state token."""


def create_oauth_state(*, provider: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=10)
    payload = {
        "typ": "oauth_state",
        "provider": provider,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "nonce": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)


def verify_oauth_state(token: str, *, expected_provider: str) -> None:
    s = get_settings()
    try:
        data = jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise OAuthStateError("invalid state") from e
    if data.get("typ") != "oauth_state":
        raise OAuthStateError("invalid state type")
    if data.get("provider") != expected_provider:
        raise OAuthStateError("provider mismatch")
