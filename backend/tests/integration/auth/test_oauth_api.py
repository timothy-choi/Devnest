"""Integration tests for OAuth start + callback (HTTP to providers mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import status

from app.libs.common.config import get_settings
from app.services.auth_service.models import OAuth, UserAuth
from app.services.user_service.models import UserProfile
from sqlmodel import select


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_OAUTH_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("GCLOUD_OAUTH_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "gh_test_id")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "gh_test_secret")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_ID", "g_test_id")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_SECRET", "g_test_secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _github_http_mocks() -> tuple[MagicMock, MagicMock]:
    """Return (post, get) side effects for GitHub token + user + emails."""

    def post(url: str, **kwargs: object) -> MagicMock:
        if "github.com/login/oauth/access_token" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {"access_token": "gh_access_token"}
            return m
        raise AssertionError(f"unexpected POST {url}")

    def get(url: str, **kwargs: object) -> MagicMock:
        if "api.github.com/user/emails" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = [{"email": "oauth_gh@example.com", "primary": True}]
            return m
        if "api.github.com/user" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {"id": 424242, "login": "oauthuser", "email": None}
            return m
        raise AssertionError(f"unexpected GET {url}")

    return post, get


def _google_http_mocks() -> tuple[MagicMock, MagicMock]:
    def post(url: str, **kwargs: object) -> MagicMock:
        if "oauth2.googleapis.com/token" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {"access_token": "g_access_token"}
            return m
        raise AssertionError(f"unexpected POST {url}")

    def get(url: str, **kwargs: object) -> MagicMock:
        if "googleapis.com/oauth2/v2/userinfo" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {
                "id": "sub-google-1",
                "email": "oauth_g@example.com",
                "name": "Google User",
            }
            return m
        raise AssertionError(f"unexpected GET {url}")

    return post, get


def test_oauth_start_github_returns_authorization_url(client) -> None:
    r = client.post("/auth/oauth/github")
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert "authorization_url" in data
    assert data["authorization_url"].startswith("https://github.com/login/oauth/authorize")


def test_oauth_start_unknown_provider_400(client) -> None:
    r = client.post("/auth/oauth/twitter")
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_oauth_github_callback_creates_user_and_sets_cookie(client, db_session, monkeypatch) -> None:
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    post_fn, get_fn = _github_http_mocks()
    monkeypatch.setattr(httpx, "post", post_fn)
    monkeypatch.setattr(httpx, "get", get_fn)

    start = client.post("/auth/oauth/github")
    auth_url = start.json()["authorization_url"]
    state = parse_qs(urlparse(auth_url).query)["state"][0]

    cb = client.get("/auth/oauth/github/callback", params={"code": "fake_code", "state": state})
    assert cb.status_code == status.HTTP_200_OK
    assert "access_token" in cb.json()
    assert cb.json()["token_type"] == "bearer"
    assert cb.cookies.get("refresh_token")

    row = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_gh@example.com")).first()
    assert row is not None
    assert row.username == "oauthuser"
    link = db_session.exec(
        select(OAuth).where(OAuth.oauth_provider == "github", OAuth.provider_user_id == "424242")
    ).first()
    assert link is not None
    assert link.user_id == row.user_auth_id


def test_oauth_github_callback_second_login_same_user(client, db_session, monkeypatch) -> None:
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    post_fn, get_fn = _github_http_mocks()
    monkeypatch.setattr(httpx, "post", post_fn)
    monkeypatch.setattr(httpx, "get", get_fn)

    start = client.post("/auth/oauth/github")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    client.get("/auth/oauth/github/callback", params={"code": "c1", "state": state})

    start2 = client.post("/auth/oauth/github")
    state2 = parse_qs(urlparse(start2.json()["authorization_url"]).query)["state"][0]
    cb2 = client.get("/auth/oauth/github/callback", params={"code": "c2", "state": state2})
    assert cb2.status_code == status.HTTP_200_OK

    users = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_gh@example.com")).all()
    assert len(users) == 1


def test_oauth_google_callback_creates_user(client, db_session, monkeypatch) -> None:
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    post_fn, get_fn = _google_http_mocks()
    monkeypatch.setattr(httpx, "post", post_fn)
    monkeypatch.setattr(httpx, "get", get_fn)

    start = client.post("/auth/oauth/google")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    cb = client.get("/auth/oauth/google/callback", params={"code": "gcode", "state": state})
    assert cb.status_code == status.HTTP_200_OK
    row = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_g@example.com")).first()
    assert row is not None

    db_session.expire_all()
    prof = db_session.exec(select(UserProfile).where(UserProfile.user_id == row.user_auth_id)).first()
    assert prof is not None


def test_oauth_callback_bad_state_400(client, monkeypatch) -> None:
    from app.services.auth_service.services.oauth_client import httpx

    monkeypatch.setattr(httpx, "post", MagicMock())
    monkeypatch.setattr(httpx, "get", MagicMock())
    r = client.get("/auth/oauth/github/callback", params={"code": "x", "state": "invalid"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_oauth_start_google_returns_authorization_url(client) -> None:
    r = client.post("/auth/oauth/google")
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert "authorization_url" in data
    assert "accounts.google.com" in data["authorization_url"]


def test_oauth_google_callback_second_login_same_user(client, db_session, monkeypatch) -> None:
    """Second Google callback with the same Google user_id must NOT create a duplicate UserAuth."""
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    post_fn, get_fn = _google_http_mocks()
    monkeypatch.setattr(httpx, "post", post_fn)
    monkeypatch.setattr(httpx, "get", get_fn)

    start1 = client.post("/auth/oauth/google")
    state1 = parse_qs(urlparse(start1.json()["authorization_url"]).query)["state"][0]
    cb1 = client.get("/auth/oauth/google/callback", params={"code": "gc1", "state": state1})
    assert cb1.status_code == status.HTTP_200_OK

    start2 = client.post("/auth/oauth/google")
    state2 = parse_qs(urlparse(start2.json()["authorization_url"]).query)["state"][0]
    cb2 = client.get("/auth/oauth/google/callback", params={"code": "gc2", "state": state2})
    assert cb2.status_code == status.HTTP_200_OK

    # Only one UserAuth row for this Google user
    users = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_g@example.com")).all()
    assert len(users) == 1, f"Expected 1 user, got {len(users)}"


def test_oauth_google_links_to_existing_email_account(client, db_session, monkeypatch) -> None:
    """Google OAuth with an email that already has a password account links them (no new UserAuth)."""
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    # Register a regular account with the same email that Google will return
    reg = client.post(
        "/auth/register",
        json={"username": "google_link_user", "email": "oauth_g_link@example.com", "password": "Pass123!"},
    )
    assert reg.status_code == status.HTTP_201_CREATED

    def post(url: str, **kwargs: object) -> MagicMock:
        if "oauth2.googleapis.com/token" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {"access_token": "g_link_token"}
            return m
        raise AssertionError(f"unexpected POST {url}")

    def get(url: str, **kwargs: object) -> MagicMock:
        if "googleapis.com/oauth2/v2/userinfo" in url:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {
                "id": "sub-google-link",
                "email": "oauth_g_link@example.com",
                "name": "Google Link User",
            }
            return m
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)

    start = client.post("/auth/oauth/google")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    cb = client.get("/auth/oauth/google/callback", params={"code": "glink", "state": state})
    assert cb.status_code == status.HTTP_200_OK

    # Must NOT have created a second UserAuth for this email
    users = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_g_link@example.com")).all()
    assert len(users) == 1, f"Expected 1 user, got {len(users)}"

    # OAuth link row must now exist
    link = db_session.exec(
        select(OAuth).where(OAuth.oauth_provider == "google", OAuth.provider_user_id == "sub-google-link")
    ).first()
    assert link is not None


def test_oauth_google_callback_creates_user_profile(client, db_session, monkeypatch) -> None:
    """Google callback must create a UserProfile row alongside UserAuth."""
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx
    from app.services.user_service.models import UserProfile

    post_fn, get_fn = _google_http_mocks()
    monkeypatch.setattr(httpx, "post", post_fn)
    monkeypatch.setattr(httpx, "get", get_fn)

    start = client.post("/auth/oauth/google")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    client.get("/auth/oauth/google/callback", params={"code": "gprof", "state": state})

    user = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_g@example.com")).first()
    assert user is not None
    db_session.expire_all()
    prof = db_session.exec(select(UserProfile).where(UserProfile.user_id == user.user_auth_id)).first()
    assert prof is not None, "UserProfile not created for Google OAuth user"


def test_oauth_google_bad_state_400(client, monkeypatch) -> None:
    """Google callback with invalid/missing state returns 400."""
    from app.services.auth_service.services.oauth_client import httpx

    monkeypatch.setattr(httpx, "post", MagicMock())
    monkeypatch.setattr(httpx, "get", MagicMock())
    r = client.get("/auth/oauth/google/callback", params={"code": "gx", "state": "bad-state-xyz"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_oauth_google_creates_oauth_row(client, db_session, monkeypatch) -> None:
    """Google callback must create an OAuth row linking provider_user_id to UserAuth."""
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    post_fn, get_fn = _google_http_mocks()
    monkeypatch.setattr(httpx, "post", post_fn)
    monkeypatch.setattr(httpx, "get", get_fn)

    start = client.post("/auth/oauth/google")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    client.get("/auth/oauth/google/callback", params={"code": "gorow", "state": state})

    link = db_session.exec(
        select(OAuth).where(OAuth.oauth_provider == "google", OAuth.provider_user_id == "sub-google-1")
    ).first()
    assert link is not None
    user = db_session.get(UserAuth, link.user_id)
    assert user is not None
    assert user.email == "oauth_g@example.com"
