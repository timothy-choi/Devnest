"""Unit tests for notification_service helpers and lookups (mocked session)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.notification_service.services import notification_service
from app.services.notification_service.services.exceptions import NotificationNotFoundError


def test_validate_priority_accepts_known_values() -> None:
    assert notification_service.validate_priority("LOW") == "LOW"
    assert notification_service.validate_priority("CRITICAL") == "CRITICAL"


def test_validate_priority_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="invalid priority"):
        notification_service.validate_priority("not-a-priority")


@patch(
    "app.services.notification_service.services.notification_service.recipient_repo.get_recipient_by_notification_and_user",
)
def test_get_notification_for_user_raises_when_no_recipient(mock_get: MagicMock) -> None:
    mock_get.return_value = None
    session = MagicMock()
    with pytest.raises(NotificationNotFoundError):
        notification_service.get_notification_for_user(session, user_id=1, notification_id=42)
