"""Docker Compose structure: bind mounts, ports, extra_hosts for host upstream."""

from pathlib import Path

import yaml


def test_compose_binds_traefik_configs_and_route_admin(gateway_root: Path) -> None:
    raw = (gateway_root / "docker-compose.yml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    traefik = cfg["services"]["traefik"]
    vols = traefik["volumes"]
    assert "./traefik/traefik.yml:/etc/traefik/traefik.yml:ro" in vols
    assert "./traefik/dynamic:/etc/traefik/dynamic:ro" in vols
    assert any("host.docker.internal:host-gateway" in str(h) for h in traefik.get("extra_hosts", []))
    ports = traefik["ports"]
    assert any(p.startswith("${DEVNEST_GATEWAY_PORT:-80}:80") or ":80" in p for p in ports)

    ra = cfg["services"]["route-admin"]
    ra_vols = ra["volumes"]
    assert "./traefik/dynamic:/etc/traefik/dynamic" in ra_vols
    ra_ports = ra["ports"]
    assert any("9080:8080" in p or "DEVNEST_ROUTE_ADMIN_PORT" in p for p in ra_ports)


def test_compose_referenced_files_exist(gateway_root: Path) -> None:
    assert (gateway_root / "traefik" / "traefik.yml").is_file()
    assert (gateway_root / "traefik" / "dynamic" / "000-base.yml").is_file()
    assert (gateway_root / "traefik" / "dynamic" / "100-workspaces.yml").is_file()
    assert (gateway_root / "docker-compose.yml").is_file()
