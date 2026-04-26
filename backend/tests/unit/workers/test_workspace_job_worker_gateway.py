"""Workspace worker gateway hooks (best-effort route-admin)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.workspace_service.models import Workspace, WorkspaceRuntime
from app.workers.workspace_job_worker import worker as wmod


@pytest.fixture
def running_workspace() -> Workspace:
    ws = MagicMock(spec=Workspace)
    ws.workspace_id = 101
    ws.public_host = None
    ws.execution_node_id = 2
    return ws


@pytest.fixture
def session_with_runtime(running_workspace: Workspace) -> MagicMock:
    rt = MagicMock(spec=WorkspaceRuntime)
    rt.node_id = "node-2"
    rt.gateway_route_target = None
    rt.internal_endpoint = "http://10.0.0.1:8080"
    sess = MagicMock()
    sess.exec.return_value.first.return_value = rt
    return sess


def test_gateway_try_register_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    running_workspace: Workspace,
    session_with_runtime: MagicMock,
) -> None:
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "false")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    try:
        with patch.object(wmod, "DevnestGatewayClient") as m:
            wmod._gateway_try_register_running(session_with_runtime, running_workspace)
            m.from_settings.assert_not_called()
    finally:
        get_settings.cache_clear()


def test_gateway_try_register_calls_client_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    running_workspace: Workspace,
    session_with_runtime: MagicMock,
) -> None:
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_GATEWAY_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("DEVNEST_BASE_DOMAIN", "app.devnest.local")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    try:
        mock_client = MagicMock()
        with patch.object(wmod, "DevnestGatewayClient") as cls:
            cls.from_settings.return_value = mock_client
            wmod._gateway_try_register_running(session_with_runtime, running_workspace)
            mock_client.register_route.assert_called_once_with(
                "101",
                "http://10.0.0.1:8080",
                "ws-101.app.devnest.local",
                node_key="node-2",
                execution_node_id=2,
            )
    finally:
        get_settings.cache_clear()


def test_gateway_try_deregister_swallows_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "true")
    from app.libs.common.config import get_settings
    from app.services.gateway_client.errors import GatewayClientTransportError

    get_settings.cache_clear()
    try:
        mock_client = MagicMock()
        mock_client.deregister_route.side_effect = GatewayClientTransportError("down")
        with patch.object(wmod, "DevnestGatewayClient") as cls:
            cls.from_settings.return_value = mock_client
            wmod._gateway_try_deregister(101)  # should not raise
            mock_client.deregister_route.assert_called_once_with("101")
    finally:
        get_settings.cache_clear()
