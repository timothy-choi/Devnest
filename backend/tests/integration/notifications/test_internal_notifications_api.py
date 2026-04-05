"""Integration tests for internal notification endpoints (X-Internal-API-Key)."""

from __future__ import annotations

from fastapi import status
from sqlmodel import select

from app.services.notification_service.models import NotificationDelivery
from app.services.notification_service.models.enums import DeliveryChannel, DeliveryStatus


INTERNAL_HEADERS = {"X-Internal-API-Key": "integration-test-internal-key"}


def _register(client, username: str, email: str) -> int:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_201_CREATED
    return r.json()["user_auth_id"]


def _create_body(recipient_user_ids: list[int]):
    return {
        "type": "internal.test",
        "title": "T",
        "body": "B",
        "recipient_user_ids": recipient_user_ids,
        "priority": "NORMAL",
        "source_service": "integration_tests",
    }


def test_internal_create_notification_returns_id_and_status(client):
    uid = _register(client, "int_create", "int_create@example.com")
    r = client.post("/internal/notifications", json=_create_body([uid]), headers=INTERNAL_HEADERS)
    assert r.status_code == status.HTTP_201_CREATED
    data = r.json()
    assert "notification_id" in data
    assert "status" in data


def test_internal_create_rejects_missing_api_key(client):
    uid = _register(client, "int_no_key", "int_no_key@example.com")
    r = client.post("/internal/notifications", json=_create_body([uid]))
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_internal_create_rejects_wrong_api_key(client):
    uid = _register(client, "int_bad_key", "int_bad_key@example.com")
    r = client.post(
        "/internal/notifications",
        json=_create_body([uid]),
        headers={"X-Internal-API-Key": "wrong-key"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_internal_create_when_key_unconfigured_returns_503(client, monkeypatch):
    from app.libs.common.config import get_settings

    monkeypatch.setenv("INTERNAL_API_KEY", "")
    get_settings.cache_clear()
    uid = _register(client, "int_503", "int_503@example.com")
    r = client.post(
        "/internal/notifications",
        json=_create_body([uid]),
        headers={"X-Internal-API-Key": "any"},
    )
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_internal_retry_unknown_delivery_404(client):
    r = client.post(
        "/internal/notifications/deliveries/999999999/retry",
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == status.HTTP_404_NOT_FOUND


def test_internal_retry_non_failed_returns_409(client, db_session):
    uid = _register(client, "int_409", "int_409@example.com")
    cr = client.post("/internal/notifications", json=_create_body([uid]), headers=INTERNAL_HEADERS)
    assert cr.status_code == status.HTTP_201_CREATED

    delivered = db_session.exec(
        select(NotificationDelivery).where(NotificationDelivery.status == DeliveryStatus.DELIVERED.value)
    ).first()
    assert delivered is not None
    assert delivered.delivery_id is not None

    rr = client.post(
        f"/internal/notifications/deliveries/{delivered.delivery_id}/retry",
        headers=INTERNAL_HEADERS,
    )
    assert rr.status_code == status.HTTP_409_CONFLICT


def test_internal_retry_failed_push_delivery(client, db_session):
    uid = _register(client, "retry_user", "retry@example.com")
    cr = client.post("/internal/notifications", json=_create_body([uid]), headers=INTERNAL_HEADERS)
    assert cr.status_code == status.HTTP_201_CREATED

    failed = db_session.exec(
        select(NotificationDelivery).where(
            NotificationDelivery.channel == DeliveryChannel.PUSH.value,
            NotificationDelivery.status == DeliveryStatus.FAILED.value,
        )
    ).first()
    assert failed is not None
    assert failed.delivery_id is not None

    rr = client.post(
        f"/internal/notifications/deliveries/{failed.delivery_id}/retry",
        headers=INTERNAL_HEADERS,
    )
    assert rr.status_code == status.HTTP_200_OK
    assert rr.json()["delivery_id"] == failed.delivery_id
    assert rr.json()["status"] == DeliveryStatus.FAILED.value
