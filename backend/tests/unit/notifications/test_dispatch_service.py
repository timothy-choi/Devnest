"""Unit tests for dispatch preference resolution (mocked preference repo)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.notification_service.models.enums import DeliveryChannel
from app.services.notification_service.models.notification_preference import NotificationPreference
from app.services.notification_service.services import dispatch_service


@patch(
    "app.services.notification_service.services.dispatch_service.preference_repo.get_preference_by_user_and_type",
)
def test_resolve_enabled_channels_all_default_when_no_row(mock_get: MagicMock) -> None:
    mock_get.return_value = None
    session = MagicMock()
    enabled = dispatch_service.resolve_enabled_channels(session, user_id=9, notification_type="billing.alert")
    assert enabled == {DeliveryChannel.IN_APP, DeliveryChannel.EMAIL, DeliveryChannel.PUSH}


@patch(
    "app.services.notification_service.services.dispatch_service.preference_repo.get_preference_by_user_and_type",
)
def test_resolve_enabled_channels_respects_preference_flags(mock_get: MagicMock) -> None:
    mock_get.return_value = NotificationPreference(
        user_id=9,
        notification_type="billing.alert",
        in_app_enabled=True,
        email_enabled=False,
        push_enabled=False,
    )
    session = MagicMock()
    enabled = dispatch_service.resolve_enabled_channels(session, user_id=9, notification_type="billing.alert")
    assert enabled == {DeliveryChannel.IN_APP}
