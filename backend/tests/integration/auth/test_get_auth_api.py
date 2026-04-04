"""Integration tests for GET /auth."""

from fastapi import status
from sqlmodel import select

from app.services.auth_service.models import UserAuth
from app.services.auth_service.services.auth_token import create_access_token


def test_get_auth_success_returns_profile(client, db_session):
    reg = client.post(
        "/auth/register",
        json={
            "username": "me_user",
            "email": "me@example.com",
            "password": "securepass123",
        },
    )
    assert reg.status_code == status.HTTP_201_CREATED
    uid = reg.json()["user_auth_id"]
    token = create_access_token(user_id=uid)

    r = client.get("/auth", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert data["user_auth_id"] == uid
    assert data["username"] == "me_user"
    assert data["email"] == "me@example.com"
    assert "created_at" in data
    assert "password" not in data


def test_get_auth_missing_authorization(client):
    r = client.get("/auth")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_auth_invalid_token(client):
    r = client.get("/auth", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_auth_user_removed_returns_401(client, db_session):
    reg = client.post(
        "/auth/register",
        json={
            "username": "gone",
            "email": "gone@example.com",
            "password": "securepass123",
        },
    )
    uid = reg.json()["user_auth_id"]
    token = create_access_token(user_id=uid)

    row = db_session.exec(select(UserAuth).where(UserAuth.user_auth_id == uid)).first()
    assert row is not None
    db_session.delete(row)
    db_session.commit()

    r = client.get("/auth", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
