"""Static Traefik file: entrypoints, file provider, dynamic path."""

from pathlib import Path

import yaml


def test_traefik_yml_parses_and_wires_file_provider(gateway_root: Path) -> None:
    raw = (gateway_root / "traefik" / "traefik.yml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    assert cfg is not None
    assert cfg["entryPoints"]["web"]["address"] == ":80"
    assert cfg["providers"]["file"]["filename"] == "/etc/traefik/dynamic.yml"
    assert cfg["providers"]["file"]["watch"] is True
