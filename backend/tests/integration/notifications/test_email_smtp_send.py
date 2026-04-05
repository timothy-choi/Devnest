"""Integration tests: real SMTP send to a minimal local TCP server (stdlib only)."""

from __future__ import annotations

import socket
import threading
import time
from email import message_from_string
from email.policy import default as email_default_policy

import pytest
from fastapi import status

from app.libs.common.config import get_settings
from app.services.auth_service.services.auth_token import create_access_token


INTERNAL_HEADERS = {"X-Internal-API-Key": "integration-test-internal-key"}


def _handle_one_smtp_client(conn: socket.socket, messages: list[str]) -> None:
    """
    Minimal SMTP dialogue for smtplib.SMTP.send_message (no TLS).
    Enough for EHLO / MAIL / RCPT / DATA / QUIT.
    """
    rb = conn.makefile("rb")
    wb = conn.makefile("wb")

    def writeln(data: bytes) -> None:
        wb.write(data + b"\r\n")
        wb.flush()

    writeln(b"220 devnest-test ESMTP")
    line = rb.readline()
    while line:
        cmd = line.strip().upper()
        if cmd.startswith(b"EHLO") or cmd.startswith(b"HELO"):
            writeln(b"250-localhost greets you")
            writeln(b"250 HELP")
        elif cmd.startswith(b"MAIL FROM"):
            writeln(b"250 OK")
        elif cmd.startswith(b"RCPT TO"):
            writeln(b"250 OK")
        elif cmd.startswith(b"DATA"):
            writeln(b"354 End data with <CR><LF>.<CR><LF>")
            body_parts: list[bytes] = []
            while True:
                data_line = rb.readline()
                if data_line in (b".\r\n", b".\n"):
                    break
                body_parts.append(data_line)
            raw = b"".join(body_parts).decode("utf-8", errors="replace")
            messages.append(raw)
            writeln(b"250 OK")
        elif cmd.startswith(b"QUIT"):
            writeln(b"221 Bye")
            break
        elif cmd.startswith(b"RSET"):
            writeln(b"250 OK")
        elif cmd.startswith(b"NOOP"):
            writeln(b"250 OK")
        else:
            writeln(b"502 Command not implemented")
        line = rb.readline()

    rb.close()
    wb.close()


class _RecordingSmtpServer(threading.Thread):
    """Listens on 127.0.0.1; records one or more DATA payloads as strings."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.messages: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()

    def run(self) -> None:
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                _handle_one_smtp_client(conn, self.messages)
            finally:
                conn.close()

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def local_smtp_server():
    srv = _RecordingSmtpServer()
    try:
        srv.start()
        time.sleep(0.05)
        yield srv, srv.port
    finally:
        srv.shutdown()
        srv.join(timeout=3)


def test_send_email_delivers_to_local_smtp_server(local_smtp_server, monkeypatch: pytest.MonkeyPatch):
    from app.services.notification_service.channels import email_channel

    srv, port = local_smtp_server
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(port))
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "noreply@devnest.test")
    get_settings.cache_clear()
    try:
        ok, mid, err = email_channel.send_email(
            rendered={
                "to": "user@example.com",
                "subject": "SMTP integration",
                "text_body": "Plain content",
                "html_body": "<p>Plain content</p>",
            },
        )
        assert ok is True
        assert err is None
        assert mid is not None
        assert mid.startswith("smtp-")
        assert len(srv.messages) == 1
        raw = srv.messages[0]
        assert "user@example.com" in raw
        assert "SMTP integration" in raw
        parsed = message_from_string(raw, policy=email_default_policy)
        bodies: list[str] = []
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    bodies.append(payload.decode("utf-8", errors="replace"))
        assert any("Plain content" in b for b in bodies)
    finally:
        get_settings.cache_clear()


def test_internal_notification_triggers_smtp_to_local_server(client, local_smtp_server, monkeypatch: pytest.MonkeyPatch):
    srv, port = local_smtp_server
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(port))
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "noreply@devnest.test")
    get_settings.cache_clear()
    try:
        reg = client.post(
            "/auth/register",
            json={
                "username": "smtp_user",
                "email": "smtp_user@example.com",
                "password": "securepass123",
            },
        )
        assert reg.status_code == status.HTTP_201_CREATED
        uid = reg.json()["user_auth_id"]

        cr = client.post(
            "/internal/notifications",
            headers=INTERNAL_HEADERS,
            json={
                "type": "email.smtp.test",
                "title": "Queued job finished",
                "body": "Your export is ready.",
                "recipient_user_ids": [uid],
                "priority": "NORMAL",
                "source_service": "integration_smtp",
            },
        )
        assert cr.status_code == status.HTTP_201_CREATED

        assert len(srv.messages) == 1
        raw = srv.messages[0]
        assert "smtp_user@example.com" in raw
        assert "Queued job finished" in raw
        parsed = message_from_string(raw, policy=email_default_policy)
        combined = ""
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    combined += payload.decode("utf-8", errors="replace")
        assert "Your export is ready." in combined

        lst = client.get("/notifications", headers={"Authorization": f"Bearer {create_access_token(user_id=uid)}"})
        assert lst.status_code == status.HTTP_200_OK
        assert lst.json()["total"] == 1
    finally:
        get_settings.cache_clear()
