"""Derive Traefik/route-admin upstream from workspace runtime (Phase 3b Step 9).

Topology ``internal_endpoint`` remains ``{workspace_ip}:{ide_port}`` for on-node probes.
``gateway_route_target`` stores ``http://{execution_host}:{published_host_port}`` when the
workspace runs on EC2 (reachable from Traefik via the node's private IP / SSH host and Docker
published port).
"""

from __future__ import annotations

from app.services.workspace_service.models import WorkspaceRuntime


def compose_traefik_upstream_target(
    *,
    traefik_routing_host: str | None,
    resolved_ports: tuple[tuple[int, int], ...],
    topology_internal_endpoint: str | None,
    ide_container_port: int,
) -> str | None:
    """
    Prefer ``http://{traefik_routing_host}:{host_port}`` when we have a node reachability host and a
    published host port for the IDE container port; else fall back to topology ``ip:port`` string.
    """
    topo = (topology_internal_endpoint or "").strip()
    host = (traefik_routing_host or "").strip()
    host_port: int | None = None
    for hp, cp in resolved_ports or ():
        try:
            if int(cp) == int(ide_container_port) and int(hp) > 0:
                host_port = int(hp)
                break
        except (TypeError, ValueError):
            continue
    if host and host_port:
        return f"http://{host}:{host_port}"
    return topo or None


def registration_upstream(
    gateway_route_target: str | None,
    internal_endpoint: str | None,
) -> str:
    """Prefer persisted Traefik target, else topology ``internal_endpoint`` (for route-admin ``target``)."""
    for candidate in (gateway_route_target, internal_endpoint):
        s = (candidate or "").strip()
        if s:
            return s
    return ""


def traefik_upstream_for_registration(rt: WorkspaceRuntime) -> str:
    """URL or host:port string to register with route-admin (never secrets)."""
    return registration_upstream(rt.gateway_route_target, rt.internal_endpoint)
