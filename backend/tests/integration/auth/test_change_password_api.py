"""Integration tests for PUT /auth/password."""

import bcrypt
from fastapi import status
from sqlmodel import select

from app.services.auth_service.models import UserAuth


def _register_and_login(client, username="pwuser", email="pwuser@example.com"):
    client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "originalpass123",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": username, "password": "originalpass123"},
    )
    assert login_r.status_code == status.HTTP_200_OK
    return login_r.json()


def test_change_password_success(client, db_session):
    data = _register_and_login(client)
    access = data["access_token"]
    refresh = data["refresh_token"]

    r = client.put(
        "/auth/password",
        headers={"Authorization": f"Bearer {access}"},
        json={"current_password": "originalpass123", "new_password": "brandnewpass99"},
    )
    assert r.status_code == status.HTTP_200_OK
    assert r.json().get("message") == "Password updated"

    row = db_session.exec(select(UserAuth).where(UserAuth.username == "pwuser")).first()
    assert row is not None
    assert bcrypt.checkpw(b"brandnewpass99", row.password_hash.encode("utf-8"))

    old_login = client.post(
        "/auth/login",
        json={"username": "pwuser", "password": "originalpass123"},
    )
    assert old_login.status_code == status.HTTP_401_UNAUTHORIZED

    new_login = client.post(
        "/auth/login",
        json={"username": "pwuser", "password": "brandnewpass99"},
    )
    assert new_login.status_code == status.HTTP_200_OK

    logout_r = client.post("/auth/logout", json={"refresh_token": refresh})
    assert logout_r.status_code == status.HTTP_400_BAD_REQUEST


def test_change_password_wrong_current_password(client):
    data = _register_and_login(client, username="wrongpw", email="wrongpw@example.com")
    r = client.put(
        "/auth/password",
        headers={"Authorization": f"Bearer {data['access_token']}"},
        json={"current_password": "notthepassword", "new_password": "newpass1234"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_change_password_requires_auth(client):
    client.post(
        "/auth/register",
        json={
            "username": "noauth",
            "email": "noauth@example.com",
            "password": "originalpass123",
        },
    )
    r = client.put(
        "/auth/password",
        json={"current_password": "originalpass123", "new_password": "newpass1234"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_change_password_preserves_other_fields(client, db_session):
    _register_and_login(client, username="samefields", email="same@example.com")
    login_r = client.post(
        "/auth/login",
        json={"username": "samefields", "password": "originalpass123"},
    )
    access = login_r.json()["access_token"]
    before = db_session.exec(select(UserAuth).where(UserAuth.username == "samefields")).first()
    assert before is not None
    uid = before.user_auth_id
    email_before = before.email

    assert (
        client.put(
            "/auth/password",
            headers={"Authorization": f"Bearer {access}"},
            json={"current_password": "originalpass123", "new_password": "newnewnew123"},
        ).status_code
        == status.HTTP_200_OK
    )

    after = db_session.exec(select(UserAuth).where(UserAuth.user_auth_id == uid)).first()
    assert after is not None
    assert after.email == email_before
    assert after.username == "samefields"
