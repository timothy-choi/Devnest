"""Unit tests for logout_refresh_token (no HTTP)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service.models import Token
from app.services.auth_service.services.logout_service import UnknownRefreshTokenError, logout_refresh_token


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=["exec", "add", "commit"])


def _exec_result(first_value):
    r = MagicMock()
    r.first.return_value = first_value
    return r


def test_logout_success_hashes_input_and_revokes_row(mock_session: MagicMock) -> None:
    row = Token(
        token_id=1,
        user_id=10,
        token_hash="will-be-looked-up-by-hash",
        expires_at=datetime.now(timezone.utc),
        revoked=False,
    )
    mock_session.exec.return_value = _exec_result(row)

    with patch(
        "app.services.auth_service.services.logout_service.hash_refresh_token",
        return_value="hashed-from-plain",
    ) as mock_hash:
        logout_refresh_token(mock_session, refresh_token="plain-refresh")

    mock_hash.assert_called_once_with("plain-refresh")
    mock_session.exec.assert_called_once()
    assert row.revoked is True
    mock_session.add.assert_called_once_with(row)
    mock_session.commit.assert_called_once()


def test_logout_unknown_token_raises(mock_session: MagicMock) -> None:
    mock_session.exec.return_value = _exec_result(None)

    with patch(
        "app.services.auth_service.services.logout_service.hash_refresh_token",
        return_value="h",
    ):
        with pytest.raises(UnknownRefreshTokenError):
            logout_refresh_token(mock_session, refresh_token="nope")

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


def test_logout_already_revoked_raises(mock_session: MagicMock) -> None:
    row = Token(
        token_id=2,
        user_id=1,
        token_hash="x",
        expires_at=datetime.now(timezone.utc),
        revoked=True,
    )
    mock_session.exec.return_value = _exec_result(row)

    with patch(
        "app.services.auth_service.services.logout_service.hash_refresh_token",
        return_value="x",
    ):
        with pytest.raises(UnknownRefreshTokenError):
            logout_refresh_token(mock_session, refresh_token="same")

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
