"""Integration: per-scope internal keys isolate surfaces (PostgreSQL app)."""

from __future__ import annotations

from app.libs.common.config import get_settings


def test_notifications_scoped_key_without_legacy(client, monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("INTERNAL_API_KEY_NOTIFICATIONS", "scoped-notif-key")
    get_settings.cache_clear()
    try:
        uid = 1
        r = client.post(
            "/internal/notifications",
            json={
                "type": "internal.test",
                "title": "t",
                "body": "b",
                "recipient_user_ids": [uid],
                "priority": "normal",
                "source_service": "test",
            },
            headers={"X-Internal-API-Key": "scoped-notif-key"},
        )
        assert r.status_code == 201, r.text
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
