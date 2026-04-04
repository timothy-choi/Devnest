"""
Cross-endpoint validation and HTTP edge cases (422 / auth headers).

Keeps endpoint-specific happy-path tests in their own modules.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import status

from app.libs.common.config import get_settings


def _assert_unprocessable(resp):
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "detail" in resp.json()


# --- POST /auth/register ---


def test_register_rejects_empty_username(client):
    _assert_unprocessable(
        client.post(
            "/auth/register",
            json={"username": "", "email": "a@b.com", "password": "12345678"},
        )
    )


def test_register_rejects_password_shorter_than_eight(client):
    _assert_unprocessable(
        client.post(
            "/auth/register",
            json={"username": "u", "email": "u@example.com", "password": "short7"},
        )
    )


def test_register_rejects_invalid_email(client):
    _assert_unprocessable(
        client.post(
            "/auth/register",
            json={"username": "u", "email": "not-an-email", "password": "12345678"},
        )
    )


def test_register_rejects_missing_required_fields(client):
    _assert_unprocessable(client.post("/auth/register", json={"username": "onlyuser"}))
    _assert_unprocessable(client.post("/auth/register", json={"email": "a@b.com", "password": "12345678"}))


def test_register_rejects_password_over_max_length(client):
    _assert_unprocessable(
        client.post(
            "/auth/register",
            json={
                "username": "u",
                "email": "u@example.com",
                "password": "x" * 257,
            },
        )
    )


def test_register_rejects_username_over_max_length(client):
    _assert_unprocessable(
        client.post(
            "/auth/register",
            json={
                "username": "x" * 256,
                "email": "u@example.com",
                "password": "12345678",
            },
        )
    )


def test_register_accepts_boundary_length_password_and_username(client):
    """password min 8, username min 1."""
    r = client.post(
        "/auth/register",
        json={
            "username": "a",
            "email": "boundary@example.com",
            "password": "12345678",
        },
    )
    assert r.status_code == status.HTTP_201_CREATED


# --- POST /auth/login ---


def test_login_rejects_empty_username(client):
    _assert_unprocessable(
        client.post("/auth/login", json={"username": "", "password": "x"}),
    )


def test_login_rejects_empty_password(client):
    _assert_unprocessable(
        client.post("/auth/login", json={"username": "u", "password": ""}),
    )


def test_login_rejects_missing_fields(client):
    _assert_unprocessable(client.post("/auth/login", json={"username": "u"}))
    _assert_unprocessable(client.post("/auth/login", json={"password": "p"}))


def test_login_rejects_username_over_max_length(client):
    _assert_unprocessable(
        client.post(
            "/auth/login",
            json={"username": "x" * 256, "password": "whatever1"},
        )
    )


def test_login_rejects_password_over_max_length(client):
    client.post(
        "/auth/register",
        json={"username": "longpu", "email": "longpu@example.com", "password": "12345678"},
    )
    _assert_unprocessable(
        client.post(
            "/auth/login",
            json={"username": "longpu", "password": "y" * 257},
        )
    )


# --- POST /auth/logout ---


def test_logout_rejects_empty_refresh_token(client):
    _assert_unprocessable(client.post("/auth/logout", json={"refresh_token": ""}))


def test_logout_rejects_missing_refresh_token(client):
    _assert_unprocessable(client.post("/auth/logout", json={}))


def test_logout_rejects_refresh_token_over_max_length(client):
    _assert_unprocessable(
        client.post("/auth/logout", json={"refresh_token": "z" * 2049}),
    )


# --- GET /auth ---


def test_get_auth_rejects_basic_scheme(client):
    r = client.get("/auth", headers={"Authorization": "Basic dGVzdA=="})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_auth_rejects_malformed_authorization(client):
    r = client.get("/auth", headers={"Authorization": "NotBearer x.y.z"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_auth_rejects_expired_jwt(client):
    s = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": "1",
        "type": "access",
        "iat": int(past.timestamp()),
        "exp": int(past.timestamp()),
    }
    token = jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    r = client.get("/auth", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_auth_rejects_jwt_wrong_type_claim(client):
    s = get_settings()
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)
    payload = {
        "sub": "1",
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(future.timestamp()),
    }
    token = jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    r = client.get("/auth", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_auth_rejects_jwt_signed_with_wrong_secret(client):
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)
    payload = {
        "sub": "1",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(future.timestamp()),
    }
    token = jwt.encode(payload, "wrong-secret-key", algorithm="HS256")
    r = client.get("/auth", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_register_rejects_malformed_json(client):
    r = client.post(
        "/auth/register",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_register_rejects_wrong_value_types(client):
    _assert_unprocessable(
        client.post(
            "/auth/register",
            json={"username": 123, "email": "a@b.com", "password": "12345678"},
        )
    )


def test_logout_rejects_null_refresh_token(client):
    _assert_unprocessable(client.post("/auth/logout", json={"refresh_token": None}))


def test_change_password_rejects_new_password_too_short(client):
    client.post(
        "/auth/register",
        json={
            "username": "valpw",
            "email": "valpw@example.com",
            "password": "12345678",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "valpw", "password": "12345678"},
    )
    access = login_r.json()["access_token"]
    _assert_unprocessable(
        client.put(
            "/auth/password",
            headers={"Authorization": f"Bearer {access}"},
            json={"current_password": "12345678", "new_password": "short7"},
        )
    )


def test_change_password_rejects_missing_fields(client):
    client.post(
        "/auth/register",
        json={
            "username": "valpw2",
            "email": "valpw2@example.com",
            "password": "12345678",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "valpw2", "password": "12345678"},
    )
    access = login_r.json()["access_token"]
    _assert_unprocessable(
        client.put(
            "/auth/password",
            headers={"Authorization": f"Bearer {access}"},
            json={"current_password": "12345678"},
        )
    )
    _assert_unprocessable(
        client.put(
            "/auth/password",
            headers={"Authorization": f"Bearer {access}"},
            json={"new_password": "12345678"},
        )
    )


def test_change_password_rejects_new_password_over_max_length(client):
    client.post(
        "/auth/register",
        json={
            "username": "valpw3",
            "email": "valpw3@example.com",
            "password": "12345678",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "valpw3", "password": "12345678"},
    )
    access = login_r.json()["access_token"]
    _assert_unprocessable(
        client.put(
            "/auth/password",
            headers={"Authorization": f"Bearer {access}"},
            json={"current_password": "12345678", "new_password": "x" * 257},
        )
    )


def test_access_token_remains_valid_after_refresh_logout(client):
    """Logout revokes refresh only; access JWT is unchanged until it expires."""
    client.post(
        "/auth/register",
        json={
            "username": "dualtok",
            "email": "dualtok@example.com",
            "password": "12345678",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "dualtok", "password": "12345678"},
    )
    assert login_r.status_code == status.HTTP_200_OK
    data = login_r.json()
    assert client.post("/auth/logout", json={"refresh_token": data["refresh_token"]}).status_code == status.HTTP_200_OK
    me = client.get("/auth", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["username"] == "dualtok"


def test_get_auth_rejects_valid_jwt_for_non_integer_sub(client):
    s = get_settings()
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)
    payload = {
        "sub": "not-a-number",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(future.timestamp()),
    }
    token = jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    r = client.get("/auth", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
