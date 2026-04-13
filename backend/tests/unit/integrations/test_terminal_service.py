"""Unit tests for terminal service — validates authentication and SSM error handling."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_ssm_mode_sends_error_and_closes():
    """SSM mode should send an informative error and close the WebSocket."""
    from app.services.integration_service.terminal_service import relay_terminal

    ws = AsyncMock()
    ws.client_state = MagicMock()
    ws.client_state.value = 1  # CONNECTED

    bundle = MagicMock()
    bundle.docker_client = None  # ssm_docker has no docker_client

    asyncio.run(relay_terminal(ws, bundle, "container123", shell="/bin/bash"))

    ws.accept.assert_called_once()
    ws.send_text.assert_called_once()
    msg = json.loads(ws.send_text.call_args[0][0])
    assert msg["type"] == "error"
    assert "SSM" in msg["message"]
    ws.close.assert_called_once()


def test_local_docker_container_not_found_raises_terminal_error():
    """If the container doesn't exist, relay_terminal raises TerminalError."""
    from app.services.integration_service.terminal_service import TerminalError, relay_terminal

    ws = AsyncMock()

    bundle = MagicMock()
    bundle.docker_client = MagicMock()
    bundle.docker_client.containers.get.side_effect = Exception("Container not found")

    with pytest.raises(TerminalError, match="container_not_found"):
        asyncio.run(relay_terminal(ws, bundle, "missing_container"))
