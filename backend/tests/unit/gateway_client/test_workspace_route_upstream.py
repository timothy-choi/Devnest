"""Unit tests: Traefik upstream composition from runtime placement hints."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.gateway_client.workspace_route_upstream import (
    compose_traefik_upstream_target,
    registration_upstream,
    traefik_upstream_for_registration,
)


def test_compose_prefers_node_host_and_published_port() -> None:
    u = compose_traefik_upstream_target(
        traefik_routing_host="10.1.2.3",
        resolved_ports=((32001, 8080),),
        topology_internal_endpoint="172.30.0.5:8080",
        ide_container_port=8080,
    )
    assert u == "http://10.1.2.3:32001"


def test_compose_falls_back_to_topology_when_no_host() -> None:
    u = compose_traefik_upstream_target(
        traefik_routing_host=None,
        resolved_ports=((32001, 8080),),
        topology_internal_endpoint="172.30.0.5:8080",
        ide_container_port=8080,
    )
    assert u == "172.30.0.5:8080"


def test_compose_falls_back_when_no_matching_publish() -> None:
    u = compose_traefik_upstream_target(
        traefik_routing_host="10.1.2.3",
        resolved_ports=((32001, 9090),),
        topology_internal_endpoint="172.30.0.5:8080",
        ide_container_port=8080,
    )
    assert u == "172.30.0.5:8080"


def test_registration_upstream_prefers_gateway_column() -> None:
    assert registration_upstream("http://10.0.0.1:32000", "172.0.0.2:8080") == "http://10.0.0.1:32000"


def test_traefik_upstream_for_registration_reads_runtime() -> None:
    rt = MagicMock()
    rt.gateway_route_target = None
    rt.internal_endpoint = "10.9.9.9:8080"
    assert traefik_upstream_for_registration(rt) == "10.9.9.9:8080"
