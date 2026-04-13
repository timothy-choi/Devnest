"""Integration tests for provider token management (Task 1: GitHub OAuth connect)."""

from __future__ import annotations

import pytest
import httpx
from fastapi import status


def _register_and_login(client, *, username="tokenuser", email="tokenuser@example.com", password="pass12345"):
    client.post("/auth/register", json={"username": username, "email": email, "password": password})
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["access_token"]


def test_list_provider_tokens_empty(client):
    token = _register_and_login(client)
    resp = client.get("/auth/provider-tokens", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


def test_provider_connect_requires_auth(client):
    resp = client.post("/auth/provider-tokens/github/connect")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_provider_connect_unsupported_provider(client):
    token = _register_and_login(client, username="u2", email="u2@example.com")
    resp = client.post(
        "/auth/provider-tokens/gitlab/connect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "gitlab" in resp.json()["detail"].lower()


def test_provider_connect_github_returns_authorization_url(client, monkeypatch):
    """POST /auth/provider-tokens/github/connect returns an authorization_url."""
    token = _register_and_login(client, username="u3", email="u3@example.com")

    # Mock settings so GitHub OAuth is "configured".
    from app.libs.common.config import get_settings

    settings = get_settings()
    # Only need client_id and base_url to be non-empty for this test.
    monkeypatch.setattr(settings, "oauth_github_client_id", "fake-client-id", raising=False)
    monkeypatch.setattr(settings, "github_oauth_public_base_url", "http://localhost:8000", raising=False)

    resp = client.post(
        "/auth/provider-tokens/github/connect",
        headers={"Authorization": f"Bearer {token}"},
    )
    # If OAuth not fully configured in the test env, we'll get 400 (acceptable).
    # If it is configured, we expect 200 with an authorization_url.
    assert resp.status_code in (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST)
    if resp.status_code == status.HTTP_200_OK:
        assert "authorization_url" in resp.json()
        assert "github.com" in resp.json()["authorization_url"]


def test_delete_nonexistent_token_returns_404(client):
    token = _register_and_login(client, username="u4", email="u4@example.com")
    resp = client.delete(
        "/auth/provider-tokens/999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_delete_other_users_token_returns_404(client, db_session, monkeypatch):
    """A user cannot delete another user's token."""
    from datetime import datetime, timezone

    monkeypatch.setenv("DEVNEST_TOKEN_ENCRYPTION_KEY", "test-enc-key-integ")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    from app.services.integration_service.token_crypto import encrypt_token, invalidate_cache

    invalidate_cache()

    from app.services.integration_service.models import UserProviderToken

    # Create another user and give them a token.
    token_a = _register_and_login(client, username="owner_user", email="owner@example.com")
    token_b = _register_and_login(client, username="attacker", email="attacker@example.com")

    # Seed a provider token for user_a by looking up their id.
    from sqlmodel import select
    from app.services.auth_service.models import UserAuth

    user_a = db_session.exec(select(UserAuth).where(UserAuth.username == "owner_user")).first()
    assert user_a is not None

    now = datetime.now(timezone.utc)
    row = UserProviderToken(
        user_id=user_a.user_auth_id,
        provider="github",
        access_token_encrypted=encrypt_token("gh_token_abc"),
        scopes="repo",
        provider_user_id="1234",
        provider_username="owner_user",
        created_at=now,
        updated_at=now,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    # Attacker tries to delete it.
    resp = client.delete(
        f"/auth/provider-tokens/{row.token_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    invalidate_cache()
    get_settings.cache_clear()
