"""Integration tests for user notification, preference, and push subscription routes."""

from __future__ import annotations

from fastapi import status

from app.services.auth_service.services.auth_token import create_access_token


INTERNAL_HEADERS = {"X-Internal-API-Key": "integration-test-internal-key"}


def _register(client, username: str, email: str) -> int:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_201_CREATED
    return r.json()["user_auth_id"]


def _bearer(uid: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id=uid)}"}


def _internal_create(client, *, recipient_user_ids: list[int], notif_type: str = "test.event", **extra):
    body = {
        "type": notif_type,
        "title": "Title",
        "body": "Body text",
        "recipient_user_ids": recipient_user_ids,
        "priority": "NORMAL",
        "source_service": "integration_tests",
        **extra,
    }
    return client.post("/internal/notifications", json=body, headers=INTERNAL_HEADERS)


def test_notifications_list_empty_requires_auth(client):
    r = client.get("/notifications")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_notifications_list_after_internal_create(client):
    uid = _register(client, "notif_user", "notif_user@example.com")
    cr = _internal_create(client, recipient_user_ids=[uid])
    assert cr.status_code == status.HTTP_201_CREATED
    nid = cr.json()["notification_id"]

    r = client.get("/notifications", headers=_bearer(uid))
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["notification_id"] == nid
    assert item["title"] == "Title"
    assert item["type"] == "test.event"
    assert item["recipient_status"] in ("PENDING", "DELIVERED", "READ")


def test_notification_detail_and_mark_read(client):
    uid = _register(client, "reader", "reader@example.com")
    cr = _internal_create(client, recipient_user_ids=[uid])
    nid = cr.json()["notification_id"]

    d = client.get(f"/notifications/{nid}", headers=_bearer(uid))
    assert d.status_code == status.HTTP_200_OK
    assert d.json()["notification_id"] == nid
    assert "status" in d.json()

    mr = client.put(f"/notifications/{nid}/read", headers=_bearer(uid))
    assert mr.status_code == status.HTTP_200_OK
    assert mr.json()["recipient_status"] == "READ"
    assert mr.json()["read_at"] is not None

    unread = client.get("/notifications", headers=_bearer(uid), params={"filter_mode": "unread"})
    assert unread.status_code == status.HTTP_200_OK
    assert unread.json()["total"] == 0

    read_list = client.get("/notifications", headers=_bearer(uid), params={"filter_mode": "read"})
    assert read_list.status_code == status.HTTP_200_OK
    assert read_list.json()["total"] == 1


def test_read_bulk_updates_matching_rows(client):
    uid = _register(client, "bulk_user", "bulk@example.com")
    n1 = _internal_create(client, recipient_user_ids=[uid]).json()["notification_id"]
    n2 = _internal_create(client, recipient_user_ids=[uid]).json()["notification_id"]

    r = client.put(
        "/notifications/read-bulk",
        headers=_bearer(uid),
        json={"notification_ids": [n1, n2]},
    )
    assert r.status_code == status.HTTP_200_OK
    ids = {row["notification_id"] for row in r.json()}
    assert ids == {n1, n2}


def test_dismiss_notification(client):
    uid = _register(client, "dismiss_user", "dismiss@example.com")
    nid = _internal_create(client, recipient_user_ids=[uid]).json()["notification_id"]

    r = client.put(f"/notifications/{nid}/dismiss", headers=_bearer(uid))
    assert r.status_code == status.HTTP_200_OK
    assert r.json()["recipient_status"] == "DISMISSED"
    assert r.json()["dismissed_at"] is not None


def test_notification_not_found_for_other_user(client):
    uid_a = _register(client, "owner", "owner@example.com")
    uid_b = _register(client, "other", "other@example.com")
    nid = _internal_create(client, recipient_user_ids=[uid_a]).json()["notification_id"]

    r = client.get(f"/notifications/{nid}", headers=_bearer(uid_b))
    assert r.status_code == status.HTTP_404_NOT_FOUND


def test_preferences_get_and_put(client):
    uid = _register(client, "pref_user", "pref@example.com")
    g = client.get("/notifications/preferences", headers=_bearer(uid))
    assert g.status_code == status.HTTP_200_OK
    assert g.json()["preferences"] == []

    p = client.put(
        "/notifications/preferences",
        headers=_bearer(uid),
        json={
            "preferences": [
                {
                    "notification_type": "billing.alert",
                    "in_app_enabled": True,
                    "email_enabled": False,
                    "push_enabled": True,
                }
            ]
        },
    )
    assert p.status_code == status.HTTP_200_OK
    prefs = p.json()["preferences"]
    assert len(prefs) == 1
    assert prefs[0]["notification_type"] == "billing.alert"
    assert prefs[0]["email_enabled"] is False


def test_push_subscriptions_register_list_revoke(client):
    uid = _register(client, "push_user", "push@example.com")

    reg = client.post(
        "/notifications/push/subscriptions",
        headers=_bearer(uid),
        json={
            "platform": "WEB",
            "endpoint": "https://push.example.com/ep/1",
            "p256dh_key": "k1",
            "auth_key": "a1",
        },
    )
    assert reg.status_code == status.HTTP_201_CREATED
    sid = reg.json()["push_subscription_id"]
    assert reg.json()["endpoint"].startswith("https://")

    lst = client.get("/notifications/push/subscriptions", headers=_bearer(uid))
    assert lst.status_code == status.HTTP_200_OK
    assert len(lst.json()) == 1

    dl = client.delete(f"/notifications/push/subscriptions/{sid}", headers=_bearer(uid))
    assert dl.status_code == status.HTTP_204_NO_CONTENT

    lst2 = client.get("/notifications/push/subscriptions", headers=_bearer(uid))
    assert lst2.status_code == status.HTTP_200_OK
    assert lst2.json() == []


def test_push_subscriptions_revoke_wrong_user_404(client):
    uid_a = _register(client, "pa", "pa@example.com")
    uid_b = _register(client, "pb", "pb@example.com")
    reg = client.post(
        "/notifications/push/subscriptions",
        headers=_bearer(uid_a),
        json={"platform": "WEB", "endpoint": "https://push.example.com/ep/x"},
    )
    sid = reg.json()["push_subscription_id"]

    r = client.delete(f"/notifications/push/subscriptions/{sid}", headers=_bearer(uid_b))
    assert r.status_code == status.HTTP_404_NOT_FOUND
