"""Unit tests for password reset service edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.auth_service.services.password_reset_service import (
    InvalidResetTokenError,
    reset_password_with_token,
)


def test_reset_password_with_token_unknown_token_raises() -> None:
    session = MagicMock()
    session.exec.return_value.first.return_value = None
    with pytest.raises(InvalidResetTokenError):
        reset_password_with_token(session, token="unknown", new_password="newpass123")
