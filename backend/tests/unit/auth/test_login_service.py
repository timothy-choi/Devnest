"""Unit tests for login_user (no HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service.models import Token, UserAuth
from app.services.auth_service.services.login_service import InvalidCredentialsError, login_user


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=["exec", "add", "commit", "refresh"])


def _exec_result(first_value):
    r = MagicMock()
    r.first.return_value = first_value
    return r


def test_login_user_success_persists_hashed_refresh_and_returns_tokens(mock_session: MagicMock) -> None:
    user = UserAuth(
        user_auth_id=5,
        username="alice",
        email="a@example.com",
        password_hash="stored-hash",
    )
    mock_session.exec.return_value = _exec_result(user)
    captured: dict[str, Token] = {}

    def add_side_effect(t: Token) -> None:
        captured["token"] = t

    def refresh_side_effect(t: Token) -> None:
        t.token_id = 100

    mock_session.add.side_effect = add_side_effect
    mock_session.refresh.side_effect = refresh_side_effect

    with (
        patch("app.services.auth_service.services.login_service.bcrypt.checkpw", return_value=True),
        patch(
            "app.services.auth_service.services.login_service.new_refresh_token_value",
            return_value="plain-refresh-secret",
        ),
        patch(
            "app.services.auth_service.services.login_service.hash_refresh_token",
            return_value="hashed-refresh-for-db",
        ),
        patch(
            "app.services.auth_service.services.login_service.create_access_token",
            return_value="jwt-access",
        ),
    ):
        result = login_user(mock_session, username="alice", password="pw")

    assert result.access_token == "jwt-access"
    assert result.refresh_token == "plain-refresh-secret"
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once()
    tok = captured["token"]
    assert tok.user_id == 5
    assert tok.token_hash == "hashed-refresh-for-db"
    assert tok.token_hash != "plain-refresh-secret"
    assert tok.revoked is False


def test_login_user_unknown_username_raises(mock_session: MagicMock) -> None:
    mock_session.exec.return_value = _exec_result(None)

    with pytest.raises(InvalidCredentialsError):
        login_user(mock_session, username="nobody", password="x")

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


def test_login_user_wrong_password_raises(mock_session: MagicMock) -> None:
    user = UserAuth(
        user_auth_id=1,
        username="alice",
        email="a@example.com",
        password_hash="hash",
    )
    mock_session.exec.return_value = _exec_result(user)

    with patch("app.services.auth_service.services.login_service.bcrypt.checkpw", return_value=False):
        with pytest.raises(InvalidCredentialsError):
            login_user(mock_session, username="alice", password="wrong")

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


def test_login_user_hashes_before_persist(mock_session: MagicMock) -> None:
    """Plain refresh must not be passed to Token.token_hash (diagram: hash then store)."""
    user = UserAuth(
        user_auth_id=2,
        username="bob",
        email="b@example.com",
        password_hash="h",
    )
    mock_session.exec.return_value = _exec_result(user)

    def capture_add(obj: object) -> None:
        if isinstance(obj, Token):
            assert obj.token_hash != "ONLY-PLAIN"
            assert obj.token_hash == "computed-hash"

    mock_session.add.side_effect = capture_add

    with (
        patch("app.services.auth_service.services.login_service.bcrypt.checkpw", return_value=True),
        patch(
            "app.services.auth_service.services.login_service.new_refresh_token_value",
            return_value="ONLY-PLAIN",
        ),
        patch(
            "app.services.auth_service.services.login_service.hash_refresh_token",
            return_value="computed-hash",
        ),
        patch("app.services.auth_service.services.login_service.create_access_token", return_value="jwt"),
    ):
        login_user(mock_session, username="bob", password="ok")

    mock_session.commit.assert_called_once()
