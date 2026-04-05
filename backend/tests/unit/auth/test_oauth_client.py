"""Unit tests for OAuth authorization URL building."""

from __future__ import annotations

import pytest

from app.libs.common.config import get_settings
from app.services.auth_service.services.oauth_client import (
    OAuthProviderError,
    build_github_authorization_url,
    build_google_authorization_url,
    normalize_oauth_public_base,
)


def test_github_authorization_url_requires_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("GITHUB_OAUTH_PUBLIC_BASE_URL", "http://api.local")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "")
    get_settings.cache_clear()
    with pytest.raises(OAuthProviderError, match="not configured"):
        build_github_authorization_url(state="abc")


def test_github_authorization_url_contains_expected_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("GITHUB_OAUTH_PUBLIC_BASE_URL", "http://api.local")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "cid_test")
    get_settings.cache_clear()
    url = build_github_authorization_url(state="st_xyz")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=cid_test" in url
    assert "state=st_xyz" in url
    assert "redirect_uri=http%3A%2F%2Fapi.local%2Fauth%2Foauth%2Fgithub%2Fcallback" in url


def test_normalize_oauth_public_base_prepends_http_for_host_only() -> None:
    assert normalize_oauth_public_base("localhost:3003") == "http://localhost:3003"
    assert normalize_oauth_public_base("  localhost:3003/  ") == "http://localhost:3003"


def test_google_authorization_url_uses_gcloud_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("GCLOUD_OAUTH_PUBLIC_BASE_URL", "localhost:3003")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_ID", "g_cid")
    get_settings.cache_clear()
    url = build_google_authorization_url(state="st")
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A3003%2Fauth%2Foauth%2Fgoogle%2Fcallback" in url
