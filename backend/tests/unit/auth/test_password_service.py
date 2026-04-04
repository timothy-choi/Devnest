"""Unit tests for change_password (no HTTP)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service.models import Token, UserAuth
from app.services.auth_service.services.password_service import InvalidCurrentPasswordError, change_password


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=["add", "commit", "refresh", "exec"])


def _exec_tokens(*tokens: Token):
    r = MagicMock()
    r.all.return_value = list(tokens)
    return r


def test_change_password_success_updates_hash_and_revokes_tokens(mock_session: MagicMock) -> None:
    user = UserAuth(
        user_auth_id=3,
        username="u",
        email="u@example.com",
        password_hash="old-hash",
    )
    tok = Token(
        token_id=1,
        user_id=3,
        token_hash="h",
        expires_at=datetime.now(timezone.utc),
        revoked=False,
    )
    mock_session.exec.return_value = _exec_tokens(tok)

    with (
        patch(
            "app.services.auth_service.services.password_service.bcrypt.checkpw",
            return_value=True,
        ),
        patch(
            "app.services.auth_service.services.password_service.bcrypt.hashpw",
            return_value=b"new-bytes",
        ),
        patch(
            "app.services.auth_service.services.password_service.bcrypt.gensalt",
            return_value=b"salt",
        ),
    ):
        change_password(mock_session, user=user, current_password="old", new_password="newpass12")

    assert user.password_hash == b"new-bytes".decode("utf-8")
    assert tok.revoked is True
    mock_session.add.assert_called()
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(user)


def test_change_password_wrong_current_raises(mock_session: MagicMock) -> None:
    user = UserAuth(
        user_auth_id=1,
        username="u",
        email="u@example.com",
        password_hash="hash",
    )

    with patch(
        "app.services.auth_service.services.password_service.bcrypt.checkpw",
        return_value=False,
    ):
        with pytest.raises(InvalidCurrentPasswordError):
            change_password(mock_session, user=user, current_password="wrong", new_password="newpass12")

    mock_session.commit.assert_not_called()


def test_change_password_revokes_only_active_tokens(mock_session: MagicMock) -> None:
    user = UserAuth(
        user_auth_id=2,
        username="u",
        email="u@example.com",
        password_hash="h",
    )
    active = Token(
        token_id=1,
        user_id=2,
        token_hash="a",
        expires_at=datetime.now(timezone.utc),
        revoked=False,
    )
    already = Token(
        token_id=2,
        user_id=2,
        token_hash="b",
        expires_at=datetime.now(timezone.utc),
        revoked=True,
    )
    mock_session.exec.return_value = _exec_tokens(active, already)

    with (
        patch("app.services.auth_service.services.password_service.bcrypt.checkpw", return_value=True),
        patch("app.services.auth_service.services.password_service.bcrypt.hashpw", return_value=b"n"),
        patch("app.services.auth_service.services.password_service.bcrypt.gensalt", return_value=b"s"),
    ):
        change_password(mock_session, user=user, current_password="c", new_password="newpass12")

    assert active.revoked is True
    assert already.revoked is True
