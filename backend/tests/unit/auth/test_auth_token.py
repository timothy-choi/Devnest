"""Unit tests for JWT access token helpers."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.libs.common.config import get_settings
from app.services.auth_service.services.auth_token import create_access_token, decode_access_user_id


def test_create_and_decode_roundtrip():
    token = create_access_token(user_id=42)
    assert decode_access_user_id(token) == 42


def test_decode_rejects_wrong_type_claim():
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    with pytest.raises(ValueError, match="not an access token"):
        decode_access_user_id(token)


def test_decode_rejects_expired_token():
    s = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    payload = {
        "sub": "1",
        "type": "access",
        "iat": int(past.timestamp()),
        "exp": int(past.timestamp()),
    }
    token = jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_user_id(token)


def test_decode_rejects_invalid_signature():
    token = create_access_token(user_id=1)
    tampered = token[:-3] + ("xxx" if token[-3:] != "xxx" else "yyy")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_user_id(tampered)
