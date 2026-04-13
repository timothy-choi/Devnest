"""WebSocket interactive terminal relay for workspace containers.

Architecture
------------
A WebSocket connection to ``WS /workspaces/{id}/terminal`` is authenticated
using the workspace session token (passed as ``?token=<plain_token>`` in the
URL — validated before upgrade, never logged).

Once authenticated and the workspace is confirmed RUNNING, an interactive PTY
session is opened inside the workspace container:

- **local_docker**: Docker SDK ``exec_create`` + ``exec_start(socket=True, tty=True)``
  opens a bidirectional raw socket to the container.
- **ssh_docker**: Paramiko SSH transport opens a channel with ``get_pty()`` and
  executes ``docker exec -it {container_id} {shell}``.
- **ssm_docker**: SSM does not support interactive TTY via Run Command. The
  connection is refused with an informative error; operators should use
  AWS Systems Manager Session Manager directly.

Protocol (client ↔ server)
---------------------------
- **Client → server binary**: raw terminal input bytes (keystroke / paste).
- **Client → server JSON** ``{"type":"resize","cols":N,"rows":N}``: PTY resize.
- **Server → client binary**: raw terminal output bytes.
- **Server → client JSON** ``{"type":"error","message":"..."}``: fatal error
  before/during session (sent before WebSocket close).

The relay runs two concurrent asyncio tasks (input forwarder + output forwarder)
and tears down cleanly when either side closes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket as _socket
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    pass  # NodeExecutionBundle forward reference

_logger = logging.getLogger(__name__)

_OUTPUT_CHUNK = 4096
_RELAY_TIMEOUT = 1800.0  # 30-minute max session


class TerminalError(Exception):
    """Fatal terminal setup error; causes WebSocket close with an error message."""


async def relay_terminal(
    websocket: WebSocket,
    bundle: "NodeExecutionBundle",  # noqa: F821
    container_id: str,
    *,
    shell: str = "/bin/bash",
    cols: int = 200,
    rows: int = 50,
) -> None:
    """Set up a PTY in the container and relay bytes between it and the WebSocket.

    This function drives the full lifecycle of the terminal session:
    1. Accept the WebSocket.
    2. Open container exec / SSH channel with PTY.
    3. Relay bidirectionally until client disconnects or container exits.
    4. Close gracefully.

    Raises ``TerminalError`` for setup failures (caller should send error JSON
    and close the WebSocket before raising further).
    """
    # Both local_docker and ssh_docker have bundle.docker_client set.
    # ssm_docker uses runtime_adapter only (docker_client is None).
    if bundle.docker_client is None:
        await websocket.accept()
        await _send_error(websocket, "SSM execution mode does not support interactive terminals. "
                          "Use AWS Systems Manager Session Manager directly.")
        await websocket.close(code=1001)
        return

    # For ssh_docker, docker_client is a docker.DockerClient connected via SSH URL.
    # The Docker exec API works the same as for local_docker — use the SDK path for both.
    await _relay_local_docker(websocket, bundle, container_id, shell=shell, cols=cols, rows=rows)


# ── Local Docker relay ────────────────────────────────────────────────────────

async def _relay_local_docker(
    websocket: WebSocket,
    bundle: "NodeExecutionBundle",  # noqa: F821
    container_id: str,
    *,
    shell: str,
    cols: int,
    rows: int,
) -> None:
    """Relay via Docker SDK exec socket."""
    try:
        docker_client = bundle.docker_client
        container = docker_client.containers.get(container_id)
    except Exception as exc:
        raise TerminalError(f"container_not_found: {exc}") from exc

    exec_id = docker_client.api.exec_create(
        container.id,
        [shell],
        stdin=True,
        stdout=True,
        stderr=True,
        tty=True,
    )
    raw_socket = docker_client.api.exec_start(
        exec_id["Id"],
        socket=True,
        tty=True,
    )
    # The Docker SDK returns an object whose ``._sock`` is the underlying TCP/Unix socket.
    sock: _socket.socket = getattr(raw_socket, "_sock", raw_socket)

    await websocket.accept()
    _logger.info("terminal_session_started", extra={"container_id": container_id, "mode": "local_docker"})

    loop = asyncio.get_running_loop()

    async def ws_to_container() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if "bytes" in msg and msg["bytes"]:
                    await loop.run_in_executor(None, sock.sendall, msg["bytes"])
                elif "text" in msg and msg["text"]:
                    _handle_resize_message(msg["text"], exec_id["Id"], docker_client)
        except (WebSocketDisconnect, Exception):
            pass

    async def container_to_ws() -> None:
        try:
            while True:
                data: bytes = await loop.run_in_executor(None, sock.recv, _OUTPUT_CHUNK)
                if not data:
                    break
                await websocket.send_bytes(data)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.wait_for(
            asyncio.gather(ws_to_container(), container_to_ws(), return_exceptions=True),
            timeout=_RELAY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        _logger.info("terminal_session_timeout", extra={"container_id": container_id})
    finally:
        try:
            sock.close()
        except Exception:
            pass
        _logger.info("terminal_session_ended", extra={"container_id": container_id, "mode": "local_docker"})


def _handle_resize_message(text: str, exec_id: str, docker_client: object) -> None:
    """Process a JSON resize message and call the Docker resize API."""
    try:
        msg = json.loads(text)
        if msg.get("type") == "resize":
            cols = int(msg.get("cols", 80))
            rows = int(msg.get("rows", 24))
            docker_client.api.exec_resize(exec_id, height=rows, width=cols)
    except Exception:
        pass  # Non-fatal — resize is best-effort.


# ── SSH Docker relay ──────────────────────────────────────────────────────────

async def _relay_ssh(
    websocket: WebSocket,
    bundle: "NodeExecutionBundle",  # noqa: F821
    container_id: str,
    *,
    shell: str,
    cols: int,
    rows: int,
) -> None:
    """Relay via Paramiko SSH channel with PTY → docker exec -it."""
    try:
        import paramiko  # noqa: PLC0415
    except ImportError as exc:
        raise TerminalError("paramiko not available for SSH terminal mode") from exc

    node = bundle.execution_node  # ExecutionNode row or None
    if node is None:
        raise TerminalError("ssh_docker mode requires an execution node")

    ssh_host = getattr(node, "ssh_host", None) or getattr(node, "host", None)
    ssh_port = int(getattr(node, "ssh_port", 22) or 22)
    ssh_user = getattr(node, "ssh_user", "root") or "root"
    ssh_key_path = getattr(node, "ssh_key_path", None)

    if not ssh_host:
        raise TerminalError("execution_node has no ssh_host configured")

    loop = asyncio.get_running_loop()

    def _open_channel() -> "paramiko.Channel":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = dict(hostname=ssh_host, port=ssh_port, username=ssh_user, timeout=15)
        if ssh_key_path:
            connect_kwargs["key_filename"] = ssh_key_path
        client.connect(**connect_kwargs)
        transport = client.get_transport()
        assert transport is not None
        channel = transport.open_session()
        channel.get_pty(term="xterm-256color", width=cols, height=rows)
        channel.exec_command(f"docker exec -it {container_id} {shell}")
        return channel

    try:
        channel = await loop.run_in_executor(None, _open_channel)
    except Exception as exc:
        raise TerminalError(f"ssh_connect_failed: {exc}") from exc

    await websocket.accept()
    _logger.info("terminal_session_started", extra={"container_id": container_id, "mode": "ssh_docker"})

    async def ws_to_ssh() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if "bytes" in msg and msg["bytes"]:
                    await loop.run_in_executor(None, channel.send, msg["bytes"])
                elif "text" in msg and msg["text"]:
                    _handle_ssh_resize(channel, msg["text"])
        except (WebSocketDisconnect, Exception):
            pass

    async def ssh_to_ws() -> None:
        try:
            while True:
                data = await loop.run_in_executor(None, channel.recv, _OUTPUT_CHUNK)
                if not data:
                    break
                await websocket.send_bytes(data)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.wait_for(
            asyncio.gather(ws_to_ssh(), ssh_to_ws(), return_exceptions=True),
            timeout=_RELAY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        _logger.info("terminal_session_timeout", extra={"container_id": container_id})
    finally:
        try:
            channel.close()
        except Exception:
            pass
        _logger.info("terminal_session_ended", extra={"container_id": container_id, "mode": "ssh_docker"})


def _handle_ssh_resize(channel: object, text: str) -> None:
    try:
        msg = json.loads(text)
        if msg.get("type") == "resize":
            cols = int(msg.get("cols", 80))
            rows = int(msg.get("rows", 24))
            channel.resize_pty(width=cols, height=rows)
    except Exception:
        pass


async def _send_error(websocket: WebSocket, message: str) -> None:
    try:
        await websocket.send_text(json.dumps({"type": "error", "message": message}))
    except Exception:
        pass
