"""Shared helpers for gateway system tests (API + route-admin HTTP)."""

from __future__ import annotations

import os
import uuid

import httpx
from fastapi import status
from fastapi.testclient import TestClient

from app.services.auth_service.services.auth_token import create_access_token


def internal_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY", "")
    assert key, "INTERNAL_API_KEY must be set"
    return {"X-Internal-API-Key": key}


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_and_token(client: TestClient, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecurePass123!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return uid, create_access_token(user_id=uid)


def create_workspace(client: TestClient, token: str, *, name: str | None = None) -> tuple[int, int]:
    r = client.post(
        "/workspaces",
        json={
            "name": name or f"gw-sys-{uuid.uuid4().hex[:10]}",
            "description": "gateway system",
            "is_private": True,
        },
        headers=auth_header(token),
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    data = r.json()
    return int(data["workspace_id"]), int(data["job_id"])


def process_job(client: TestClient, job_id: int) -> None:
    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": job_id},
        headers=internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert body["processed_count"] == 1
    assert body["last_job_id"] == job_id


def route_admin_base_url() -> str:
    port = os.environ.get("ROUTE_ADMIN_SYSTEM_PORT", "19080")
    return f"http://127.0.0.1:{port}".rstrip("/")


def traefik_public_url() -> str:
    port = os.environ.get("TRAEFIK_SYSTEM_PORT", "18080")
    return f"http://127.0.0.1:{port}".rstrip("/")


def fetch_registered_routes() -> list[dict]:
    base = route_admin_base_url()
    r = httpx.get(f"{base}/routes", timeout=15.0)
    r.raise_for_status()
    data = r.json()
    assert isinstance(data, list)
    return data


def route_for_workspace(routes: list[dict], workspace_id: int) -> dict | None:
    sid = str(workspace_id)
    for row in routes:
        if row.get("workspace_id") == sid:
            return row
    return None
