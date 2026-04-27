"""Short-lived JWT for snapshot archive GET without repeating full Bearer auth (local storage fallback)."""

from __future__ import annotations

import time
from typing import Any

import jwt

from app.libs.common.config import get_settings

_TOKEN_TYP = "snapshot_archive_dl"


def create_snapshot_archive_download_token(
    *,
    workspace_id: int,
    snapshot_id: int,
    user_auth_id: int,
    ttl_seconds: int = 600,
) -> str:
    s = get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "typ": _TOKEN_TYP,
        "wid": int(workspace_id),
        "sid": int(snapshot_id),
        "uid": int(user_auth_id),
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)


def decode_snapshot_archive_download_token(token: str) -> dict[str, Any]:
    s = get_settings()
    data = jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    if data.get("typ") != _TOKEN_TYP:
        raise jwt.InvalidTokenError("wrong token type")
    return data
