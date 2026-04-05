"""Unit tests for refresh_access_token (no HTTP)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service.models import Token
from app.services.auth_service.services.refresh_token_service import InvalidRefreshTokenError, refresh_access_token


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=["exec"])


def _exec_result(first_value):
    r = MagicMock()
    r.first.return_value = first_value
    return r


def test_refresh_success_returns_new_access_jwt(mock_session: MagicMock) -> None:
    future = datetime.now(timezone.utc) + timedelta(days=1)
    row = Token(
        token_id=1,
        user_id=99,
        token_hash="h",
        expires_at=future,
        revoked=False,
    )
    mock_session.exec.return_value = _exec_result(row)

    with (
        patch(
            "app.services.auth_service.services.refresh_token_service.hash_refresh_token",
            return_value="h",
        ),
        patch(
            "app.services.auth_service.services.refresh_token_service.create_access_token",
            return_value="new-jwt",
        ) as mock_create,
    ):
        out = refresh_access_token(mock_session, refresh_token_plain="plain")

    assert out == "new-jwt"
    mock_create.assert_called_once_with(user_id=99)


def test_refresh_unknown_token_raises(mock_session: MagicMock) -> None:
    mock_session.exec.return_value = _exec_result(None)

    with patch(
        "app.services.auth_service.services.refresh_token_service.hash_refresh_token",
        return_value="x",
    ):
        with pytest.raises(InvalidRefreshTokenError):
            refresh_access_token(mock_session, refresh_token_plain="nope")


def test_refresh_revoked_raises(mock_session: MagicMock) -> None:
    row = Token(
        token_id=1,
        user_id=1,
        token_hash="h",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        revoked=True,
    )
    mock_session.exec.return_value = _exec_result(row)

    with patch(
        "app.services.auth_service.services.refresh_token_service.hash_refresh_token",
        return_value="h",
    ):
        with pytest.raises(InvalidRefreshTokenError):
            refresh_access_token(mock_session, refresh_token_plain="p")


def test_refresh_expired_raises(mock_session: MagicMock) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    row = Token(
        token_id=1,
        user_id=1,
        token_hash="h",
        expires_at=past,
        revoked=False,
    )
    mock_session.exec.return_value = _exec_result(row)

    with patch(
        "app.services.auth_service.services.refresh_token_service.hash_refresh_token",
        return_value="h",
    ):
        with pytest.raises(InvalidRefreshTokenError):
            refresh_access_token(mock_session, refresh_token_plain="p")
