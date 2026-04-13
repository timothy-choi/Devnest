"""Integration tests for the workspace terminal WebSocket endpoint.

These tests validate authentication and access control. Full relay testing
requires a running Docker container and is covered in system tests.

Note on Starlette TestClient WebSocket behaviour
-------------------------------------------------
When the server closes a WebSocket *before* accepting the upgrade (i.e. calls
``websocket.close(code=...)`` without a preceding ``websocket.accept()``),
Starlette's ``WebSocketTestSession.__enter__`` raises ``WebSocketDisconnect``
immediately.  The tests below use ``pytest.raises(WebSocketDisconnect)`` to
assert that the server rejected the connection as expected.
"""

from __future__ import annotations

import pytest
from fastapi import status
from starlette.websockets import WebSocketDisconnect


def _register_and_login(client, *, username, email, password="pass12345"):
    client.post("/auth/register", json={"username": username, "email": email, "password": password})
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["access_token"]


def _create_workspace(client, token, *, name="ws_term"):
    resp = client.post(
        "/workspaces",
        json={"name": name, "description": "terminal test", "config": {"image": "nginx:alpine"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (201, 202)
    return resp.json()["workspace_id"]


def test_terminal_websocket_rejects_missing_token(client):
    """WebSocket without ?token= parameter returns 422 (required query param)."""
    resp = client.get("/workspaces/1/terminal")
    # HTTP fallback for a WebSocket-only route returns 400 or 422.
    assert resp.status_code in (400, 422)


def test_terminal_websocket_invalid_jwt_closes_with_policy_violation(client):
    """Invalid token results in immediate WebSocket close (code 4001 — policy violation)."""
    token = _register_and_login(client, username="wsterm1", email="wsterm1@example.com")
    ws_id = _create_workspace(client, token, name="ws_term_1")

    # The server closes without accepting — TestClient raises WebSocketDisconnect.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/workspaces/{ws_id}/terminal?token=bad.jwt.token"):
            pass  # pragma: no cover

    assert exc_info.value.code in (4001, 1001, 1000)


def test_terminal_websocket_nonexistent_workspace(client):
    """WebSocket to a nonexistent workspace_id closes immediately (code 4001)."""
    token = _register_and_login(client, username="wsterm2", email="wsterm2@example.com")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/workspaces/99999/terminal?token={token}"):
            pass  # pragma: no cover

    assert exc_info.value.code in (4001, 1001, 1000)


def test_terminal_websocket_stopped_workspace(client):
    """Terminal to a non-RUNNING workspace closes immediately (code 1001 — going away)."""
    token = _register_and_login(client, username="wsterm3", email="wsterm3@example.com")
    ws_id = _create_workspace(client, token, name="ws_term_3")

    # Workspace is in CREATING state (job queued, not yet RUNNING).
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/workspaces/{ws_id}/terminal?token={token}"):
            pass  # pragma: no cover

    # Expect going-away (1001) since the workspace exists but is not RUNNING.
    assert exc_info.value.code in (1001, 1000)
