"""Unit tests: dedicated execution-node HTTP heartbeat emitter."""

from __future__ import annotations

import pytest

from app.libs.common.config import get_settings
from app.services.placement_service.node_heartbeat import internal_api_execution_node_heartbeat_post_url


@pytest.fixture(autouse=True)
def _clear_settings_after_emitter_tests() -> None:
    yield
    get_settings.cache_clear()


def test_internal_api_execution_node_heartbeat_post_url_normalizes_base() -> None:
    assert internal_api_execution_node_heartbeat_post_url("http://backend:8000") == (
        "http://backend:8000/internal/execution-nodes/heartbeat"
    )
    assert internal_api_execution_node_heartbeat_post_url("http://backend:8000/internal/execution-nodes") == (
        "http://backend:8000/internal/execution-nodes/heartbeat"
    )


def test_emit_one_heartbeat_http_posts_to_internal_route(
    workspace_job_worker_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_API_BASE_URL", "http://api.test")
    monkeypatch.setenv("INTERNAL_API_KEY", "secret-key")
    monkeypatch.setenv("DEVNEST_NODE_KEY", "node-1")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    def fake_post(url: str, json=None, headers=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class Resp:
            status_code = 200
            text = ""

        return Resp()

    monkeypatch.setattr("httpx.post", fake_post)

    from app.workers.execution_node_heartbeat_emitter import _emit_one_heartbeat_http

    ok, detail = _emit_one_heartbeat_http(
        workspace_job_worker_engine,
        base_url="http://api.test",
        node_key="node-1",
    )
    assert ok is True
    assert not detail
    assert str(captured["url"]).endswith("/internal/execution-nodes/heartbeat")
    assert captured["json"]["node_key"] == "node-1"
    assert captured["headers"].get("X-Internal-API-Key") == "secret-key"
