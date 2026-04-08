"""Unit tests for password reset request and completion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest

from app.services.auth_service.models import PasswordResetToken, Token, UserAuth
from app.services.auth_service.services.password_reset_service import (
    InvalidResetTokenError,
    request_password_reset,
    reset_password_with_token,
)
from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token


def _exec_first(value):
    r = MagicMock()
    r.first.return_value = value
    return r


def _exec_all(values):
    r = MagicMock()
    r.all.return_value = values
    return r


def test_reset_password_with_token_unknown_token_raises() -> None:
    session = MagicMock()
    session.exec.return_value.first.return_value = None
    with pytest.raises(InvalidResetTokenError):
        reset_password_with_token(session, token="unknown", new_password="newpass123")


def test_reset_password_with_token_expired_raises() -> None:
    session = MagicMock()
    row = MagicMock()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.exec.return_value.first.return_value = row
    with pytest.raises(InvalidResetTokenError):
        reset_password_with_token(session, token="tok", new_password="newpass123")


def test_reset_password_with_token_missing_user_raises() -> None:
    session = MagicMock()
    row = MagicMock()
    row.user_id = 99
    row.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    session.exec.return_value.first.return_value = row
    session.get.return_value = None
    with pytest.raises(InvalidResetTokenError):
        reset_password_with_token(session, token="raw-token-value", new_password="newpass123")


def test_reset_password_with_token_success_updates_password_revokes_tokens() -> None:
    raw = "reset-secret-token"
    row = PasswordResetToken(
        password_reset_token_id=1,
        user_id=7,
        token_hash=hash_refresh_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        used=False,
    )
    user = UserAuth(
        user_auth_id=7,
        username="alice",
        email="alice@example.com",
        password_hash="old-hash",
    )
    rt = Token(
        token_id=10,
        user_id=7,
        token_hash="rh",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        revoked=False,
    )
    session = MagicMock()
    session.exec.side_effect = [
        _exec_first(row),
        _exec_all([rt]),
    ]
    session.get.return_value = user

    reset_password_with_token(session, token=raw, new_password="newpass123")

    assert user.password_hash != "old-hash"
    assert bcrypt.checkpw(b"newpass123", user.password_hash.encode("utf-8"))
    assert row.used is True
    assert rt.revoked is True
    session.add.assert_called()
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(user)


def test_request_password_reset_unknown_email_returns_none_no_commit() -> None:
    session = MagicMock()
    session.exec.return_value.first.return_value = None
    out = request_password_reset(session, email="nobody@example.com")
    assert out is None
    session.commit.assert_not_called()


@patch("app.services.auth_service.services.password_reset_service.get_settings")
def test_request_password_reset_marks_pending_used_and_commits_new_token(mock_settings: MagicMock) -> None:
    mock_settings.return_value = MagicMock(password_reset_token_expire_minutes=90)
    user = UserAuth(user_auth_id=3, username="bob", email="bob@example.com", password_hash="h")
    pending = PasswordResetToken(
        password_reset_token_id=5,
        user_id=3,
        token_hash="old",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        used=False,
    )
    session = MagicMock()
    session.exec.side_effect = [
        _exec_first(user),
        _exec_all([pending]),
    ]

    raw = request_password_reset(session, email="bob@example.com")

    assert raw is not None
    assert isinstance(raw, str)
    assert pending.used is True
    session.commit.assert_called_once()
    added = [c[0][0] for c in session.add.call_args_list]
    new_tokens = [x for x in added if isinstance(x, PasswordResetToken) and x is not pending]
    assert len(new_tokens) == 1
    assert new_tokens[0].used is False
    assert new_tokens[0].user_id == 3
    assert new_tokens[0].token_hash == hash_refresh_token(raw)
