"""Unit tests for OAuth state JWT."""

from __future__ import annotations

import pytest

from app.libs.common.config import get_settings
from app.services.auth_service.services.oauth_state import OAuthStateError, create_oauth_state, verify_oauth_state


def test_oauth_state_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    get_settings.cache_clear()
    state = create_oauth_state(provider="github")
    verify_oauth_state(state, expected_provider="github")


def test_oauth_state_wrong_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    get_settings.cache_clear()
    state = create_oauth_state(provider="github")
    with pytest.raises(OAuthStateError):
        verify_oauth_state(state, expected_provider="google")


def test_oauth_state_invalid_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    get_settings.cache_clear()
    with pytest.raises(OAuthStateError):
        verify_oauth_state("not-a-jwt", expected_provider="github")
