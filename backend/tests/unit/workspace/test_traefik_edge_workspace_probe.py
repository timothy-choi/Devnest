"""Unit tests: Traefik edge readiness probe for workspace attach/access."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.workspace_service.errors import WorkspaceGatewayUnavailableError
from app.services.workspace_service.services import workspace_intent_service as wis


def test_traefik_probe_502_raises_gateway_unavailable() -> None:
    settings = SimpleNamespace(devnest_gateway_traefik_http_probe_base="http://127.0.0.1:81")
    resp = MagicMock(status_code=502)
    client_cm = MagicMock()
    client_cm.__enter__.return_value.get.return_value = resp
    client_cm.__exit__.return_value = None

    with patch.object(wis.httpx, "Client", return_value=client_cm):
        with pytest.raises(WorkspaceGatewayUnavailableError, match="IDE upstream"):
            wis._ensure_traefik_edge_observes_host(
                settings,
                host_header_source="ws-1.example.test",
                session=None,
                workspace_id=None,
                correlation_id=None,
            )


def test_traefik_probe_502_with_session_calls_enqueue(monkeypatch) -> None:
    settings = SimpleNamespace(devnest_gateway_traefik_http_probe_base="http://127.0.0.1:81")
    resp = MagicMock(status_code=502)
    client_cm = MagicMock()
    client_cm.__enter__.return_value.get.return_value = resp
    client_cm.__exit__.return_value = None

    calls: list[tuple[int, str | None]] = []

    def fake_enqueue(session, *, workspace_id: int, correlation_id: str | None = None) -> None:
        calls.append((workspace_id, correlation_id))

    monkeypatch.setattr(wis, "_best_effort_enqueue_reconcile_for_access_drift", fake_enqueue)

    with patch.object(wis.httpx, "Client", return_value=client_cm):
        with pytest.raises(WorkspaceGatewayUnavailableError):
            wis._ensure_traefik_edge_observes_host(
                settings,
                host_header_source="ws-1.example.test",
                session=MagicMock(),
                workspace_id=42,
                correlation_id="cid",
            )

    assert calls == [(42, "cid")]


def test_traefik_probe_404_deadline_calls_enqueue(monkeypatch) -> None:
    settings = SimpleNamespace(devnest_gateway_traefik_http_probe_base="http://127.0.0.1:81")
    resp = MagicMock(status_code=404)
    client_cm = MagicMock()
    client_cm.__enter__.return_value.get.return_value = resp
    client_cm.__exit__.return_value = None

    tick = {"t": -0.25}

    def mono() -> float:
        tick["t"] += 0.25
        return tick["t"]

    monkeypatch.setattr(wis.time, "monotonic", mono)
    monkeypatch.setattr(wis.time, "sleep", lambda *_: None)

    calls: list[int] = []

    def fake_enqueue(session, *, workspace_id: int, correlation_id: str | None = None) -> None:
        calls.append(workspace_id)

    monkeypatch.setattr(wis, "_best_effort_enqueue_reconcile_for_access_drift", fake_enqueue)

    with patch.object(wis.httpx, "Client", return_value=client_cm):
        with pytest.raises(WorkspaceGatewayUnavailableError, match="Traefik"):
            wis._ensure_traefik_edge_observes_host(
                settings,
                host_header_source="ws-1.example.test",
                session=MagicMock(),
                workspace_id=7,
                correlation_id=None,
            )

    assert calls == [7]


def test_traefik_probe_skips_when_base_unset() -> None:
    settings = SimpleNamespace(devnest_gateway_traefik_http_probe_base="")
    wis._ensure_traefik_edge_observes_host(
        settings,
        host_header_source="ws-1.example.test",
        session=None,
        workspace_id=None,
        correlation_id=None,
    )
