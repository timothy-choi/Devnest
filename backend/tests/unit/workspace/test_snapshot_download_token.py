"""Snapshot archive download JWT (local storage fallback)."""

from __future__ import annotations

import time

import jwt
import pytest

from app.libs.common.config import get_settings
from app.services.workspace_service.services.snapshot_download_token import (
    create_snapshot_archive_download_token,
    decode_snapshot_archive_download_token,
)


def test_create_and_decode_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-snapshot-dl-secret")
    get_settings.cache_clear()
    token = create_snapshot_archive_download_token(workspace_id=7, snapshot_id=42, user_auth_id=99, ttl_seconds=120)
    data = decode_snapshot_archive_download_token(token)
    assert data["wid"] == 7
    assert data["sid"] == 42
    assert data["uid"] == 99
    assert data["exp"] >= int(time.time()) + 100


def test_decode_rejects_wrong_typ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-snapshot-dl-secret-2")
    get_settings.cache_clear()
    s = get_settings()
    bad = jwt.encode({"typ": "other", "exp": int(time.time()) + 60}, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    with pytest.raises(jwt.InvalidTokenError):
        decode_snapshot_archive_download_token(bad)
