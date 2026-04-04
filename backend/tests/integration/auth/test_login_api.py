"""Integration tests for POST /auth/login."""

import hashlib

from fastapi import status
from sqlmodel import select

from app.services.auth_service.models import Token, UserAuth


def test_login_success_returns_access_and_refresh(client, db_session):
    client.post(
        "/auth/register",
        json={
            "username": "loginner",
            "email": "loginner@example.com",
            "password": "securepass123",
        },
    )
    r = client.post(
        "/auth/login",
        json={"username": "loginner", "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data.get("token_type") == "bearer"
    assert len(data["refresh_token"]) > 20

    user = db_session.exec(select(UserAuth).where(UserAuth.username == "loginner")).first()
    assert user is not None
    expected_hash = hashlib.sha256(data["refresh_token"].encode("utf-8")).hexdigest()
    row = db_session.exec(select(Token).where(Token.user_id == user.user_auth_id)).first()
    assert row is not None
    assert row.token_hash == expected_hash
    assert row.token_hash != data["refresh_token"]
    assert row.revoked is False


def test_login_wrong_password(client):
    client.post(
        "/auth/register",
        json={
            "username": "u1",
            "email": "u1@example.com",
            "password": "rightpassword",
        },
    )
    r = client.post(
        "/auth/login",
        json={"username": "u1", "password": "wrongpassword"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_unknown_user(client):
    r = client.post(
        "/auth/login",
        json={"username": "ghost", "password": "whatever1"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_access_token_works_for_get_auth(client):
    client.post(
        "/auth/register",
        json={
            "username": "tokuser",
            "email": "tok@example.com",
            "password": "securepass123",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "tokuser", "password": "securepass123"},
    )
    access = login_r.json()["access_token"]
    me = client.get("/auth", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["username"] == "tokuser"
