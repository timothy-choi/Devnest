"""Integration: per-scope internal keys isolate surfaces (PostgreSQL app)."""

from __future__ import annotations

import uuid

from fastapi import status

from app.libs.common.config import get_settings


def _register_user(client, username: str, email: str) -> int:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_201_CREATED
    return r.json()["user_auth_id"]


def test_notifications_scoped_key_without_legacy(client, monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("INTERNAL_API_KEY_NOTIFICATIONS", "scoped-notif-key")
    get_settings.cache_clear()
    try:
        suffix = uuid.uuid4().hex[:12]
        uid = _register_user(client, f"scope_notif_{suffix}", f"scope_notif_{suffix}@example.com")
        r = client.post(
            "/internal/notifications",
            json={
                "type": "internal.test",
                "title": "t",
                "body": "b",
                "recipient_user_ids": [uid],
                "priority": "NORMAL",
                "source_service": "test",
            },
            headers={"X-Internal-API-Key": "scoped-notif-key"},
        )
        assert r.status_code == status.HTTP_201_CREATED, r.text
    finally:
        get_settings.cache_clear()


def test_workspace_jobs_rejects_notification_scoped_key(client, monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("INTERNAL_API_KEY_NOTIFICATIONS", "scoped-notif-key")
    get_settings.cache_clear()
    try:
        r = client.post(
            "/internal/workspace-jobs/process",
            headers={"X-Internal-API-Key": "scoped-notif-key"},
        )
        assert r.status_code == 503
    finally:
        get_settings.cache_clear()
