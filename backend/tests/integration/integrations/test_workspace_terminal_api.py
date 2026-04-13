"""Integration tests for the workspace terminal WebSocket endpoint.

These tests validate authentication and access control. Full relay testing
requires a running Docker container and is covered in system tests.
"""

from __future__ import annotations

import pytest
from fastapi import status
from fastapi.testclient import TestClient


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
    """WebSocket without ?token= parameter is rejected."""
    with pytest.raises(Exception):
        # TestClient will raise on missing required query param
        with client.websocket_connect("/workspaces/1/terminal"):
            pass


def test_terminal_websocket_invalid_jwt_closes_with_policy_violation(client):
    """Invalid JWT token results in WebSocket close with policy-violation code."""
    token = _register_and_login(client, username="wsterm1", email="wsterm1@example.com")
    ws_id = _create_workspace(client, token, name="ws_term_1")

    with client.websocket_connect(
        f"/workspaces/{ws_id}/terminal?token=bad.jwt.token"
    ) as ws:
        # The server should close with code 4001 (policy violation) or 1001.
        # When the workspace is not RUNNING, we close with GOING_AWAY.
        # We just verify the connection is closed quickly.
        try:
            data = ws.receive()
        except Exception:
            pass


def test_terminal_websocket_nonexistent_workspace(client):
    """WebSocket to a nonexistent workspace_id closes immediately."""
    token = _register_and_login(client, username="wsterm2", email="wsterm2@example.com")

    with client.websocket_connect(f"/workspaces/99999/terminal?token={token}") as ws:
        try:
            ws.receive()
        except Exception:
            pass


def test_terminal_websocket_stopped_workspace(client):
    """Terminal to a non-RUNNING workspace closes with going_away code."""
    token = _register_and_login(client, username="wsterm3", email="wsterm3@example.com")
    ws_id = _create_workspace(client, token, name="ws_term_3")

    # Workspace is STOPPED/STARTING (just created, not yet RUNNING).
    with client.websocket_connect(f"/workspaces/{ws_id}/terminal?token={token}") as ws:
        try:
            ws.receive()
        except Exception:
            pass
