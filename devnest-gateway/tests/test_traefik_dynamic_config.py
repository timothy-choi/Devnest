"""V1 example routes: host rules and upstream URLs (data-plane contract)."""

from pathlib import Path

import yaml


def test_dynamic_yml_example_routes(gateway_root: Path) -> None:
    raw = (gateway_root / "traefik" / "dynamic.yml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    http = cfg["http"]
    routers = http["routers"]
    services = http["services"]

    assert routers["devnest-ws-123"]["rule"] == "Host(`ws-123.app.devnest.local`)"
    assert routers["devnest-ws-123"]["entryPoints"] == ["web"]
    assert routers["devnest-ws-123"]["service"] == "devnest-ws-123-upstream"

    assert routers["devnest-smoke-whoami"]["rule"] == "Host(`whoami.app.devnest.local`)"
    assert routers["devnest-smoke-whoami"]["service"] == "devnest-mock-upstream"

    ws_servers = services["devnest-ws-123-upstream"]["loadBalancer"]["servers"]
    assert ws_servers[0]["url"] == "http://host.docker.internal:8080"

    mock_servers = services["devnest-mock-upstream"]["loadBalancer"]["servers"]
    assert mock_servers[0]["url"] == "http://mock-upstream:80"
