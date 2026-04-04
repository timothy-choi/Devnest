import bcrypt
from fastapi import status
from sqlmodel import select

from app.services.auth_service.models import UserAuth


def test_register_success_creates_user(client, db_session):
    payload = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "securepass123",
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["username"] == payload["username"]
    assert data["email"] == payload["email"]
    assert "user_auth_id" in data
    assert "created_at" in data
    assert "password" not in data

    row = db_session.exec(select(UserAuth).where(UserAuth.username == "newuser")).first()
    assert row is not None
    assert row.email == payload["email"]
    assert row.user_auth_id == data["user_auth_id"]


def test_register_duplicate_email_rejected(client):
    body = {
        "username": "user_a",
        "email": "same@example.com",
        "password": "securepass123",
    }
    assert client.post("/auth/register", json=body).status_code == status.HTTP_201_CREATED

    body2 = {
        "username": "user_b",
        "email": "same@example.com",
        "password": "anotherpass456",
    }
    r2 = client.post("/auth/register", json=body2)
    assert r2.status_code == status.HTTP_409_CONFLICT
    assert "email" in r2.json()["detail"].lower() or "registered" in r2.json()["detail"].lower()


def test_register_duplicate_username_rejected(client):
    body = {
        "username": "taken_name",
        "email": "one@example.com",
        "password": "securepass123",
    }
    assert client.post("/auth/register", json=body).status_code == status.HTTP_201_CREATED

    body2 = {
        "username": "taken_name",
        "email": "two@example.com",
        "password": "anotherpass456",
    }
    r2 = client.post("/auth/register", json=body2)
    assert r2.status_code == status.HTTP_409_CONFLICT
    assert "username" in r2.json()["detail"].lower() or "registered" in r2.json()["detail"].lower()


def test_register_password_stored_hashed_not_plaintext(client, db_session):
    plain = "mysecretpassword"
    client.post(
        "/auth/register",
        json={"username": "hashtest", "email": "hashtest@example.com", "password": plain},
    )
    row = db_session.exec(select(UserAuth).where(UserAuth.username == "hashtest")).first()
    assert row is not None
    assert row.password_hash != plain
    assert bcrypt.checkpw(plain.encode("utf-8"), row.password_hash.encode("utf-8"))
