"""Derive Traefik/route-admin upstream from workspace runtime (Phase 3b Step 9).

Route-admin ``target`` (Traefik upstream) is derived **only** from persisted placement — no
hardcoded execution node ids:

1. ``WorkspaceRuntime.gateway_route_target`` — e.g. ``http://{node_private_ip}:{published_port}`` for EC2.
2. ``WorkspaceRuntime.internal_endpoint`` — topology/container address (often ``ip:port``).
3. ``Workspace.endpoint_ref`` — control-plane copy of the last known attach endpoint (fallback).

``Workspace.public_host`` is used for the Traefik **Host** rule (via route-admin ``public_host``),
not for upstream IP selection.

Topology ``internal_endpoint`` remains ``{workspace_ip}:{ide_port}`` for on-node probes; when
distinct from the Traefik-reachable URL, persist ``gateway_route_target`` from bring-up.
"""

from __future__ import annotations

from app.services.workspace_service.models.workspace import Workspace
from app.services.workspace_service.models.workspace_runtime import WorkspaceRuntime


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


def traefik_upstream_for_workspace_gateway(ws: Workspace | None, rt: WorkspaceRuntime) -> str:
    """Traefik upstream ``target`` for route-admin from runtime + optional workspace fallback (Step 9)."""
    u = registration_upstream(rt.gateway_route_target, rt.internal_endpoint)
    if u:
        return u
    if ws is not None:
        er = (ws.endpoint_ref or "").strip()
        if er:
            return er
    return ""


def traefik_upstream_for_registration(rt: WorkspaceRuntime) -> str:
    """URL or host:port string to register with route-admin when no ``Workspace`` row is loaded."""
    return traefik_upstream_for_workspace_gateway(None, rt)
