"""Integration tests for GET /auth/refresh_token."""

from fastapi import status


def test_refresh_token_success_returns_access_only(client):
    client.post(
        "/auth/register",
        json={
            "username": "refuser",
            "email": "refuser@example.com",
            "password": "12345678",
        },
    )
    login_r = client.post(
        "/auth/login",
        json={"username": "refuser", "password": "12345678"},
    )
    assert login_r.status_code == status.HTTP_200_OK
    refresh = login_r.json()["refresh_token"]

    r = client.get("/auth/refresh_token", params={"refresh_token": refresh})
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert "access_token" in data
    assert data.get("token_type") == "bearer"
    assert "refresh_token" not in data

    me = client.get("/auth", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["username"] == "refuser"


def test_refresh_token_accepts_x_refresh_token_header(client):
    client.post(
        "/auth/register",
        json={
            "username": "hdruser",
            "email": "hdr@example.com",
            "password": "12345678",
        },
    )
    refresh = client.post(
        "/auth/login",
        json={"username": "hdruser", "password": "12345678"},
    ).json()["refresh_token"]

    r = client.get("/auth/refresh_token", headers={"X-Refresh-Token": refresh})
    assert r.status_code == status.HTTP_200_OK
    assert "access_token" in r.json()


def test_refresh_token_missing_returns_400(client):
    r = client.get("/auth/refresh_token")
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_refresh_token_invalid_returns_401(client):
    r = client.get("/auth/refresh_token", params={"refresh_token": "not-a-real-token"})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_token_after_logout_returns_401(client):
    client.post(
        "/auth/register",
        json={
            "username": "revokedref",
            "email": "revokedref@example.com",
            "password": "12345678",
        },
    )
    data = client.post(
        "/auth/login",
        json={"username": "revokedref", "password": "12345678"},
    ).json()
    refresh = data["refresh_token"]
    assert client.post("/auth/logout", json={"refresh_token": refresh}).status_code == status.HTTP_200_OK

    r = client.get("/auth/refresh_token", params={"refresh_token": refresh})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
