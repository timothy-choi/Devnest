"""Integration tests for POST /auth/logout."""

import hashlib

from fastapi import status
from sqlmodel import select

from app.services.auth_service.models import Token


def _register_and_login(client):
    client.post(
        "/auth/register",
        json={
            "username": "logout_user",
            "email": "logout@example.com",
            "password": "securepass123",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "logout_user", "password": "securepass123"},
    )
    assert login_r.status_code == status.HTTP_200_OK
    return login_r.json()


def test_logout_revokes_refresh_token_in_db(client, db_session):
    data = _register_and_login(client)
    refresh = data["refresh_token"]

    out = client.post("/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == status.HTTP_200_OK
    assert out.json().get("message") == "Logged out"

    h = hashlib.sha256(refresh.encode("utf-8")).hexdigest()
    row = db_session.exec(select(Token).where(Token.token_hash == h)).first()
    assert row is not None
    assert row.revoked is True


def test_logout_twice_second_call_fails(client):
    data = _register_and_login(client)
    refresh = data["refresh_token"]

    assert client.post("/auth/logout", json={"refresh_token": refresh}).status_code == status.HTTP_200_OK
    r2 = client.post("/auth/logout", json={"refresh_token": refresh})
    assert r2.status_code == status.HTTP_400_BAD_REQUEST


def test_logout_unknown_refresh_token(client):
    r = client.post("/auth/logout", json={"refresh_token": "not-a-valid-token-value"})
    assert r.status_code == status.HTTP_400_BAD_REQUEST
