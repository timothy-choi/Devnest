"""Unit tests: worker heartbeat emit (HTTP vs DB transport)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.placement_service.bootstrap import ensure_default_local_execution_node
from app.services.placement_service.node_heartbeat import emit_default_local_execution_node_heartbeat


@pytest.fixture(autouse=True)
def _clear_settings_after_heartbeat_emit_tests() -> None:
    yield
    get_settings.cache_clear()


def test_emit_posts_internal_http_when_base_url_set(
    infrastructure_unit_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_WORKER_HEARTBEAT_INTERNAL_API_BASE_URL", "http://api.internal")
    monkeypatch.setenv("INTERNAL_API_KEY", "secret-infra-key")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    def fake_post(url: str, json=None, headers=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class Resp:
            status_code = 200
            text = "ok"

        return Resp()

    monkeypatch.setattr("httpx.post", fake_post)

    with Session(infrastructure_unit_engine) as session:
        ensure_default_local_execution_node(session)
        session.commit()
        emit_default_local_execution_node_heartbeat(session)

    assert "url" in captured
    assert str(captured["url"]).endswith("/internal/execution-nodes/heartbeat")
    assert captured["json"]["node_key"]
    assert captured["headers"].get("X-Internal-API-Key") == "secret-infra-key"
