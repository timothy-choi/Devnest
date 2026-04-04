"""Unit tests for get_user_auth_entry (no HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.auth_profile_service import (
    UserAuthNotFoundError,
    get_user_auth_entry,
)


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=["get"])


def test_get_user_auth_entry_returns_user(mock_session: MagicMock) -> None:
    user = UserAuth(
        user_auth_id=7,
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
    )
    mock_session.get.return_value = user

    result = get_user_auth_entry(mock_session, user_id=7)

    assert result is user
    mock_session.get.assert_called_once_with(UserAuth, 7)


def test_get_user_auth_entry_not_found_raises(mock_session: MagicMock) -> None:
    mock_session.get.return_value = None

    with pytest.raises(UserAuthNotFoundError):
        get_user_auth_entry(mock_session, user_id=99)

    mock_session.get.assert_called_once_with(UserAuth, 99)
