"""Integration tests for DELETE /auth/account."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import status
from sqlmodel import select

from app.libs.common.config import get_settings
from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.auth_token import create_access_token
from app.services.user_service.models import UserProfile


@pytest.fixture(autouse=True)
def _oauth_env_for_github_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match ``test_oauth_api`` so POST /auth/oauth/github returns an authorization URL in CI."""
    monkeypatch.setenv("GITHUB_OAUTH_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("GCLOUD_OAUTH_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "gh_test_id")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "gh_test_secret")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_ID", "g_test_id")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_SECRET", "g_test_secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_delete_account_local_user_with_password_removes_auth_and_profile(client, db_session):
    reg = client.post(
        "/auth/register",
        json={
            "username": "delete_me",
            "email": "delete_me@example.com",
            "password": "securepass123",
        },
    )
    assert reg.status_code == status.HTTP_201_CREATED
    uid = reg.json()["user_auth_id"]
    token = create_access_token(user_id=uid)

    r = client.request(
        "DELETE",
        "/auth/account",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "securepass123"},
    )
    assert r.status_code == status.HTTP_200_OK
    assert r.json().get("message")

    db_session.expire_all()
    assert db_session.get(UserAuth, uid) is None
    assert db_session.exec(select(UserProfile).where(UserProfile.user_id == uid)).first() is None


def test_delete_account_local_user_wrong_password_401(client):
    reg = client.post(
        "/auth/register",
        json={
            "username": "no_delete",
            "email": "no_delete@example.com",
            "password": "securepass123",
        },
    )
    token = create_access_token(user_id=reg.json()["user_auth_id"])

    r = client.request(
        "DELETE",
        "/auth/account",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "wrong-password"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # User still exists
    assert client.get("/auth", headers={"Authorization": f"Bearer {token}"}).status_code == status.HTTP_200_OK


def test_delete_account_local_user_missing_password_401(client):
    reg = client.post(
        "/auth/register",
        json={
            "username": "need_pw",
            "email": "need_pw@example.com",
            "password": "securepass123",
        },
    )
    uid = reg.json()["user_auth_id"]
    token = create_access_token(user_id=uid)

    r = client.request(
        "DELETE",
        "/auth/account",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_delete_account_oauth_user_without_password_succeeds(client, db_session, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from app.services.auth_service.services.oauth_client import httpx

    def post(url: str, **kwargs: object):
        m = MagicMock()
        if "github.com/login/oauth/access_token" in url:
            m.status_code = 200
            m.json.return_value = {"access_token": "tok"}
            return m
        raise AssertionError(url)

    def get(url: str, **kwargs: object):
        m = MagicMock()
        if "api.github.com/user/emails" in url:
            m.status_code = 200
            m.json.return_value = [{"email": "oauth_del@example.com", "primary": True}]
            return m
        if "api.github.com/user" in url:
            m.status_code = 200
            m.json.return_value = {"id": 999001, "login": "oauth_del_user", "email": None}
            return m
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)

    start = client.post("/auth/oauth/github")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    cb = client.get("/auth/oauth/github/callback", params={"code": "c_del", "state": state})
    assert cb.status_code == status.HTTP_200_OK
    token = cb.json()["access_token"]

    row = db_session.exec(select(UserAuth).where(UserAuth.email == "oauth_del@example.com")).first()
    assert row is not None
    uid = row.user_auth_id

    r = client.request(
        "DELETE",
        "/auth/account",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert r.status_code == status.HTTP_200_OK

    db_session.expire_all()
    assert db_session.get(UserAuth, uid) is None


def test_delete_account_requires_authentication(client):
    r = client.request("DELETE", "/auth/account", json={"password": "x"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
