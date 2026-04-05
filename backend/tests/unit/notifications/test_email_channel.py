"""Unit tests for notification email rendering and SMTP send (mocked transport)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.libs.common.config import get_settings
from app.services.notification_service.channels import email_channel
from app.services.notification_service.models import Notification


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_send_email_stub_when_smtp_host_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    get_settings.cache_clear()
    ok, mid, err = email_channel.send_email(
        rendered={"to": "a@b.com", "subject": "S", "text_body": "T", "html_body": "<p>T</p>"},
    )
    assert ok is True
    assert mid == "stub-email-message-id"
    assert err is None


@patch("app.services.notification_service.channels.email_channel.smtplib.SMTP")
def test_send_email_uses_smtp_when_configured(mock_smtp_class: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__.return_value = mock_server

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "from@example.test")
    get_settings.cache_clear()

    rendered = {
        "to": "user@example.test",
        "subject": "Hello",
        "text_body": "Plain body",
        "html_body": "<p>Plain body</p>",
    }
    ok, mid, err = email_channel.send_email(rendered=rendered)

    assert ok is True
    assert err is None
    assert mid is not None
    assert mid.startswith("smtp-")
    mock_smtp_class.assert_called_once_with("smtp.example.test", 587, timeout=30)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_not_called()
    mock_server.send_message.assert_called_once()
    sent = mock_server.send_message.call_args[0][0]
    assert sent["To"] == "user@example.test"
    assert sent["Subject"] == "Hello"


@patch("app.services.notification_service.channels.email_channel.smtplib.SMTP")
def test_send_email_smtp_logs_in_when_user_set(mock_smtp_class: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__.return_value = mock_server

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("SMTP_USER", "u1")
    monkeypatch.setenv("SMTP_PASSWORD", "p1")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "from@example.test")
    get_settings.cache_clear()

    ok, _, err = email_channel.send_email(
        rendered={
            "to": "to@example.test",
            "subject": "S",
            "text_body": "T",
            "html_body": "<p>T</p>",
        },
    )
    assert ok is True
    assert err is None
    mock_server.starttls.assert_not_called()
    mock_server.login.assert_called_once_with("u1", "p1")


def test_send_email_smtp_fails_without_from_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "")
    get_settings.cache_clear()

    ok, mid, err = email_channel.send_email(
        rendered={"to": "a@b.com", "subject": "S", "text_body": "T", "html_body": "<p>T</p>"},
    )
    assert ok is False
    assert mid is None
    assert err is not None
    assert "from_address" in err.lower()


def test_build_mime_message_sets_parts() -> None:
    n = Notification(
        type="t",
        title="Subject line",
        body="Line one",
        source_service="svc",
        priority="NORMAL",
    )
    rendered = email_channel.render_email(notification=n, to_email="recv@test.dev")
    msg = email_channel._build_mime_message(rendered=rendered, from_address="from@test.dev")
    assert msg["From"] == "from@test.dev"
    assert msg["To"] == "recv@test.dev"
    assert msg["Subject"] == "Subject line"
