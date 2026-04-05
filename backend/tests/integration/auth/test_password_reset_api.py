"""Integration tests for forgot-password and reset-password."""

from __future__ import annotations

import pytest
from fastapi import status
from sqlmodel import select

from app.libs.common.config import get_settings
from app.services.auth_service.models import PasswordResetToken, UserAuth


@pytest.fixture(autouse=True)
def password_reset_return_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSWORD_RESET_RETURN_TOKEN", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_forgot_password_unknown_email_still_200_no_token(client) -> None:
    r = client.put("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert "message" in data
    assert data.get("reset_token") is None


def test_forgot_then_reset_then_login(client, db_session) -> None:
    client.post(
        "/auth/register",
        json={"username": "reset_me", "email": "reset_me@example.com", "password": "oldpass123"},
    )

    r = client.put("/auth/forgot-password", json={"email": "reset_me@example.com"})
    assert r.status_code == status.HTTP_200_OK
    token = r.json().get("reset_token")
    assert token

    rr = client.put(
        "/auth/reset-password",
        json={"token": token, "new_password": "newpass999"},
    )
    assert rr.status_code == status.HTTP_200_OK

    login = client.post("/auth/login", json={"username": "reset_me", "password": "newpass999"})
    assert login.status_code == status.HTTP_200_OK

    bad = client.post("/auth/login", json={"username": "reset_me", "password": "oldpass123"})
    assert bad.status_code == status.HTTP_401_UNAUTHORIZED


def test_reset_password_invalid_token_400(client) -> None:
    r = client.put(
        "/auth/reset-password",
        json={"token": "not-valid-token", "new_password": "newpass123"},
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_forgot_password_invalidates_prior_token(client, db_session) -> None:
    client.post(
        "/auth/register",
        json={"username": "twice", "email": "twice@example.com", "password": "pass12345"},
    )
    t1 = client.put("/auth/forgot-password", json={"email": "twice@example.com"}).json()["reset_token"]
    t2 = client.put("/auth/forgot-password", json={"email": "twice@example.com"}).json()["reset_token"]
    assert t1 != t2

    r_old = client.put("/auth/reset-password", json={"token": t1, "new_password": "new111111"})
    assert r_old.status_code == status.HTTP_400_BAD_REQUEST

    r_new = client.put("/auth/reset-password", json={"token": t2, "new_password": "new222222"})
    assert r_new.status_code == status.HTTP_200_OK

    user = db_session.exec(select(UserAuth).where(UserAuth.email == "twice@example.com")).first()
    assert user is not None
    used = db_session.exec(select(PasswordResetToken).where(PasswordResetToken.used == True)).all()  # noqa: E712
    assert len(used) >= 1
