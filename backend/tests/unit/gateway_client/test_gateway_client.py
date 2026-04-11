"""Unit tests: DevnestGatewayClient with httpx.MockTransport (no route-admin process)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.libs.common.config import Settings
from app.services.gateway_client.errors import GatewayClientHTTPError, GatewayClientTransportError
from app.services.gateway_client.gateway_client import DevnestGatewayClient


def _parse_request_json(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8")) if request.content else {}


def test_register_route_posts_expected_json() -> None:
    captured: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, str(request.url.path), _parse_request_json(request)))
        return httpx.Response(
            200,
            json={
                "workspace_id": "5",
                "public_host": "5.app.devnest.local",
                "target": "http://10.0.0.5:8080",
            },
        )

    transport = httpx.MockTransport(handler)
    client = DevnestGatewayClient("http://route-admin.test", transport=transport)
    client.register_route("5", "10.0.0.5:8080", "5.app.devnest.local")

    assert len(captured) == 1
    method, path, body = captured[0]
    assert method == "POST"
    assert path == "/routes"
    assert body == {
        "workspace_id": "5",
        "public_host": "5.app.devnest.local",
        "target": "http://10.0.0.5:8080",
    }


def test_deregister_route_deletes_path() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    client = DevnestGatewayClient("http://route-admin.test", transport=transport)
    client.deregister_route("42")

    assert paths == ["/routes/42"]


def test_register_route_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    client = DevnestGatewayClient("http://route-admin.test", transport=transport)
    with pytest.raises(GatewayClientHTTPError):
        client.register_route("1", "http://x", "1.app.devnest.local")


def test_register_route_transport_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    transport = httpx.MockTransport(handler)
    client = DevnestGatewayClient("http://route-admin.test", transport=transport)
    with pytest.raises(GatewayClientTransportError):
        client.register_route("1", "http://x", "1.app.devnest.local")


def test_from_settings_uses_gateway_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DEVNEST_GATEWAY_URL", "http://custom:9999")
    s = Settings()
    c = DevnestGatewayClient.from_settings(s)
    assert c._base == "http://custom:9999"


def test_get_registered_routes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/routes"
        return httpx.Response(200, json=[{"workspace_id": "1", "public_host": "x", "target": "y"}])

    transport = httpx.MockTransport(handler)
    client = DevnestGatewayClient("http://route-admin.test", transport=transport)
    rows = client.get_registered_routes()
    assert rows == [{"workspace_id": "1", "public_host": "x", "target": "y"}]


def test_full_register_get_deregister_cycle_mock_transport() -> None:
    """End-to-end client flow against an in-memory mock HTTP peer (no DB / no route-admin)."""
    store: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "POST" and path == "/routes":
            body = json.loads(request.content.decode("utf-8"))
            wid = body["workspace_id"]
            store[wid] = body
            return httpx.Response(200, json=body)
        if method == "GET" and path == "/routes":
            return httpx.Response(200, json=list(store.values()))
        if method == "DELETE" and path.startswith("/routes/"):
            wid = path.split("/routes/", 1)[1]
            store.pop(wid, None)
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = DevnestGatewayClient("http://gw-int.test", transport=transport)

    client.register_route("10", "http://10.0.0.10:8080", "10.app.devnest.local")
    routes = client.get_registered_routes()
    assert len(routes) == 1
    client.deregister_route("10")
    routes2 = client.get_registered_routes()
    assert routes2 == []
