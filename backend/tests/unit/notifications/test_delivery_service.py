"""Unit tests for delivery retry and error paths (mocked repositories)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.notification_service.models import NotificationDelivery
from app.services.notification_service.models.enums import DeliveryStatus
from app.services.notification_service.services import delivery_service
from app.services.notification_service.services.exceptions import DeliveryNotFoundError, InvalidDeliveryStateError


@patch("app.services.notification_service.services.delivery_service.delivery_repo.get_delivery_by_id")
def test_retry_delivery_raises_when_missing(mock_get: MagicMock) -> None:
    mock_get.return_value = None
    session = MagicMock()
    with pytest.raises(DeliveryNotFoundError):
        delivery_service.retry_delivery(session, delivery_id=1)


@patch("app.services.notification_service.services.delivery_service.delivery_repo.get_delivery_by_id")
def test_retry_delivery_raises_when_not_failed(mock_get: MagicMock) -> None:
    d = NotificationDelivery(
        notification_id=1,
        notification_recipient_id=1,
        channel="IN_APP",
        status=DeliveryStatus.DELIVERED.value,
    )
    mock_get.return_value = d
    session = MagicMock()
    with pytest.raises(InvalidDeliveryStateError):
        delivery_service.retry_delivery(session, delivery_id=1)
