"""Static Traefik file: entrypoints, file provider, dynamic directory."""

from pathlib import Path

import yaml


def test_traefik_yml_parses_and_wires_file_provider(gateway_root: Path) -> None:
    raw = (gateway_root / "traefik" / "traefik.yml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    assert cfg is not None
    assert cfg["entryPoints"]["web"]["address"] == ":80"
    assert cfg["entryPoints"]["websecure"]["address"] == ":443"
    assert cfg["providers"]["file"]["directory"] == "/etc/traefik/dynamic"
    assert cfg["providers"]["file"]["watch"] is True
    le = cfg["certificatesResolvers"]["letsencrypt"]["acme"]
    assert le["tlsChallenge"] == {}
    assert le["storage"] == "/etc/traefik/acme/acme.json"
    assert le["email"] == "tchoi720@gmail.com"
    dns = cfg["certificatesResolvers"]["letsencrypt-dns"]["acme"]
    assert dns["dnsChallenge"]["provider"] == "cloudflare"
    assert dns["storage"] == "/etc/traefik/acme/acme-dns.json"
