"""Unit tests for register_user (no HTTP, no TestClient)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.register_service import (
    DuplicateEmailError,
    DuplicateUsernameError,
    register_user,
)


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=["exec", "add", "commit", "refresh"])


def _exec_result(first_value):
    r = MagicMock()
    r.first.return_value = first_value
    return r


def test_register_user_success_assigns_id_and_persists(mock_session: MagicMock) -> None:
    mock_session.exec.side_effect = [
        _exec_result(None),
        _exec_result(None),
    ]

    captured: dict[str, UserAuth] = {}

    def add_side_effect(u: UserAuth) -> None:
        captured["user"] = u

    def commit_side_effect() -> None:
        captured["user"].user_auth_id = 42

    mock_session.add.side_effect = add_side_effect
    mock_session.commit.side_effect = commit_side_effect

    user = register_user(
        mock_session,
        username="alice",
        email="alice@example.com",
        password="validpass1",
    )

    assert user is captured["user"]
    assert user.user_auth_id == 42
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.password_hash != "validpass1"
    mock_session.add.assert_called_once_with(user)
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(user)
    assert mock_session.exec.call_count == 2


def test_register_user_duplicate_username_raises(mock_session: MagicMock) -> None:
    existing = MagicMock(spec=UserAuth)
    mock_session.exec.return_value = _exec_result(existing)

    with pytest.raises(DuplicateUsernameError):
        register_user(
            mock_session,
            username="taken",
            email="a@example.com",
            password="validpass1",
        )

    mock_session.exec.assert_called_once()
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


def test_register_user_duplicate_email_raises(mock_session: MagicMock) -> None:
    mock_session.exec.side_effect = [
        _exec_result(None),
        _exec_result(MagicMock(spec=UserAuth)),
    ]

    with pytest.raises(DuplicateEmailError):
        register_user(
            mock_session,
            username="newname",
            email="exists@example.com",
            password="validpass1",
        )

    assert mock_session.exec.call_count == 2
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


@patch("app.services.auth_service.services.register_service.bcrypt.gensalt", return_value=b"fake_salt")
@patch("app.services.auth_service.services.register_service.bcrypt.hashpw")
def test_register_user_hashes_password_before_add(
    mock_hashpw: MagicMock,
    mock_gensalt: MagicMock,
    mock_session: MagicMock,
) -> None:
    mock_hashpw.return_value = b"hashed_secret"

    mock_session.exec.side_effect = [
        _exec_result(None),
        _exec_result(None),
    ]

    captured: dict[str, UserAuth] = {}

    def add_side_effect(u: UserAuth) -> None:
        captured["user"] = u

    def commit_side_effect() -> None:
        captured["user"].user_auth_id = 1

    mock_session.add.side_effect = add_side_effect
    mock_session.commit.side_effect = commit_side_effect

    register_user(
        mock_session,
        username="bob",
        email="bob@example.com",
        password="plain-secret",
    )

    mock_hashpw.assert_called_once_with(b"plain-secret", b"fake_salt")
    mock_gensalt.assert_called_once()
    assert captured["user"].password_hash == "hashed_secret"
    assert captured["user"].password_hash != "plain-secret"
