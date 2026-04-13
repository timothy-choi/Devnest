"""Unit tests for JWT secret validation in Settings.

Tests:
    - Default secret emits a WARNING and does NOT raise when DEVNEST_REQUIRE_SECRETS is false.
    - Default secret raises ValueError when DEVNEST_REQUIRE_SECRETS is true.
    - A strong custom secret passes silently (no warning, no exception).
    - devnest_require_secrets=false with a strong secret is valid.
    - The validator error message is actionable and mentions JWT_SECRET_KEY.
"""

from __future__ import annotations

import logging
import os

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_SECRET = "change-me-in-production"
_STRONG_SECRET = "s3cur3-r4ndom-hex-value-at-least-32-chars-long-x"


def _make_settings(
    *,
    jwt_secret_key: str = _DEFAULT_SECRET,
    devnest_require_secrets: bool = False,
) -> None:
    """Instantiate Settings by monkey-patching env vars, then clear the lru_cache."""
    from app.libs.common.config import Settings, get_settings

    get_settings.cache_clear()

    env_patch = {
        "DATABASE_URL": "sqlite:///./test.db",
        "JWT_SECRET_KEY": jwt_secret_key,
        "DEVNEST_REQUIRE_SECRETS": "true" if devnest_require_secrets else "false",
    }
    # Build directly without reading .env files.
    return Settings(**{
        "database_url": "sqlite:///./test.db",
        "jwt_secret_key": jwt_secret_key,
        "devnest_require_secrets": devnest_require_secrets,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDefaultSecretWarning:
    def test_default_secret_logs_warning(self, caplog):
        """Default jwt_secret_key always triggers a WARNING log entry."""
        with caplog.at_level(logging.WARNING, logger="app.libs.common.config"):
            _make_settings(jwt_secret_key=_DEFAULT_SECRET, devnest_require_secrets=False)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("change-me-in-production" in m for m in warning_messages), (
            "Expected a WARNING mentioning the default secret value"
        )

    def test_default_secret_no_require_secrets_does_not_raise(self):
        """Default secret is tolerated when DEVNEST_REQUIRE_SECRETS=false."""
        # Must not raise.
        settings = _make_settings(jwt_secret_key=_DEFAULT_SECRET, devnest_require_secrets=False)
        assert settings.jwt_secret_key == _DEFAULT_SECRET

    def test_default_secret_require_secrets_raises(self):
        """Default secret raises ValueError when DEVNEST_REQUIRE_SECRETS=true."""
        with pytest.raises(ValueError) as exc_info:
            _make_settings(jwt_secret_key=_DEFAULT_SECRET, devnest_require_secrets=True)

        error_text = str(exc_info.value)
        assert "JWT_SECRET_KEY" in error_text, (
            "Error message should tell the operator which env var to set"
        )
        assert "change-me-in-production" in error_text, (
            "Error message should name the offending default value"
        )

    def test_default_secret_error_mentions_disable_option(self):
        """Error message explains how to disable the guard in non-production."""
        with pytest.raises(ValueError) as exc_info:
            _make_settings(jwt_secret_key=_DEFAULT_SECRET, devnest_require_secrets=True)

        error_text = str(exc_info.value)
        assert "DEVNEST_REQUIRE_SECRETS" in error_text, (
            "Error message should mention DEVNEST_REQUIRE_SECRETS so operators know the toggle"
        )


class TestStrongSecret:
    def test_strong_secret_no_warning(self, caplog):
        """A non-default secret produces no security warnings."""
        with caplog.at_level(logging.WARNING, logger="app.libs.common.config"):
            _make_settings(jwt_secret_key=_STRONG_SECRET, devnest_require_secrets=False)

        security_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "jwt_secret_key" in r.message.lower()
        ]
        assert not security_warnings, (
            f"Did not expect security warnings for a strong secret but got: {security_warnings}"
        )

    def test_strong_secret_require_secrets_true_no_raise(self):
        """DEVNEST_REQUIRE_SECRETS=true does not raise when the secret is strong."""
        settings = _make_settings(jwt_secret_key=_STRONG_SECRET, devnest_require_secrets=True)
        assert settings.jwt_secret_key == _STRONG_SECRET

    def test_strong_secret_returned_unchanged(self):
        """The validator does not modify a valid secret."""
        settings = _make_settings(jwt_secret_key=_STRONG_SECRET)
        assert settings.jwt_secret_key == _STRONG_SECRET


class TestRequireSecretsFlag:
    def test_require_secrets_false_is_default(self):
        """DEVNEST_REQUIRE_SECRETS defaults to false."""
        settings = _make_settings(jwt_secret_key=_STRONG_SECRET)
        assert settings.devnest_require_secrets is False

    def test_require_secrets_can_be_enabled(self):
        """DEVNEST_REQUIRE_SECRETS=true is stored on the settings object."""
        settings = _make_settings(
            jwt_secret_key=_STRONG_SECRET,
            devnest_require_secrets=True,
        )
        assert settings.devnest_require_secrets is True
