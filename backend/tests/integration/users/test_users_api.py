"""Integration tests for GET/PATCH /users/me and GET /users/{id}."""

from __future__ import annotations

from fastapi import status

from app.services.auth_service.services.auth_token import create_access_token


def _register(client, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_201_CREATED
    data = r.json()
    uid = data["user_auth_id"]
    token = create_access_token(user_id=uid)
    return uid, token


def test_get_users_me_success_returns_full_profile(client):
    uid, token = _register(client, username="prof_me", email="prof_me@example.com")

    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert data["user_id"] == uid
    assert data["display_name"] == ""
    assert "first_name" in data
    assert "last_name" in data
    assert "bio" in data
    assert "avatar_url" in data
    assert "timezone" in data
    assert "locale" in data
    assert "created_at" in data
    assert "updated_at" in data
    assert "email" not in data


def test_get_users_me_missing_authorization_401(client):
    r = client.get("/users/me")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_users_me_invalid_token_401(client):
    r = client.get("/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_patch_users_me_updates_allowed_fields(client):
    uid, token = _register(client, username="patch_me", email="patch_me@example.com")

    r = client.patch(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "display_name": "Display Name",
            "first_name": "Pat",
            "last_name": "Chen",
            "bio": "Hello world",
            "avatar_url": "https://example.com/avatar.png",
            "timezone": "America/Los_Angeles",
            "locale": "en-CA",
        },
    )
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert data["user_id"] == uid
    assert data["display_name"] == "Display Name"
    assert data["first_name"] == "Pat"
    assert data["last_name"] == "Chen"
    assert data["bio"] == "Hello world"
    assert data["avatar_url"] == "https://example.com/avatar.png"
    assert data["timezone"] == "America/Los_Angeles"
    assert data["locale"] == "en-CA"

    g = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert g.status_code == status.HTTP_200_OK
    assert g.json()["display_name"] == "Display Name"


def test_patch_users_me_partial_only_changes_sent_fields(client):
    _, token = _register(client, username="partial_u", email="partial_u@example.com")

    client.patch(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "First pass", "bio": "Bio one"},
    )
    client.patch(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"bio": "Bio two"},
    )

    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    assert data["display_name"] == "First pass"
    assert data["bio"] == "Bio two"


def test_patch_users_me_unauthorized_401(client):
    r = client.patch("/users/me", json={"display_name": "x"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_users_public_returns_only_public_safe_fields(client):
    uid_a, token_a = _register(client, username="public_a", email="public_a@example.com")
    _register(client, username="public_b", email="public_b@example.com")

    client.patch(
        "/users/me",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "display_name": "Public A",
            "first_name": "A",
            "last_name": "User",
            "bio": "About A",
            "avatar_url": "https://example.com/a.png",
            "timezone": "UTC",
            "locale": "en",
        },
    )

    r = client.get(f"/users/{uid_a}")
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert set(data.keys()) == {
        "user_id",
        "display_name",
        "first_name",
        "last_name",
        "bio",
        "avatar_url",
    }
    assert data["user_id"] == uid_a
    assert data["display_name"] == "Public A"
    assert data["first_name"] == "A"
    assert data["last_name"] == "User"
    assert data["bio"] == "About A"
    assert data["avatar_url"] == "https://example.com/a.png"
    assert "timezone" not in data
    assert "locale" not in data
    assert "created_at" not in data
    assert "updated_at" not in data
    assert "email" not in data


def test_get_users_public_not_found_returns_404(client):
    r = client.get("/users/999999999")
    assert r.status_code == status.HTTP_404_NOT_FOUND
