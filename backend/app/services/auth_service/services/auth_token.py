"""JWT access tokens (used by GET /auth and future login)."""

from datetime import datetime, timedelta, timezone

import jwt

from app.libs.common.config import get_settings


def create_access_token(*, user_id: int) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=s.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)


def decode_access_user_id(token: str) -> int:
    s = get_settings()
    data = jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    if data.get("type") != "access":
        raise ValueError("not an access token")
    return int(data["sub"])
