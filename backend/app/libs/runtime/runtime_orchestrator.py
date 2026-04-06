"""Internal runtime orchestration (adapter sequencing only; no DB, no topology, no HTTP)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import ContainerCreateError, ContainerStartError
from .interfaces import RuntimeAdapter
from .models import EnsureRunningRuntimeResult


def ensure_running_runtime_only(
    runtime: RuntimeAdapter,
    *,
    name: str,
    image: str | None = None,
    cpu_limit: float | None = None,
    memory_limit_bytes: int | None = None,
    env: Mapping[str, str] | None = None,
    ports: Sequence[tuple[int, int]] | None = None,
    labels: Mapping[str, str] | None = None,
    workspace_host_path: str | None = None,
    existing_container_id: str | None = None,
) -> EnsureRunningRuntimeResult:
    """
    Ensure a workspace container exists, start it, then return inspection + netns snapshot.

    Not registered as an API route. Callers persist workspace state separately if needed.
    """
    ensure_res = runtime.ensure_container(
        name=name,
        image=image,
        cpu_limit=cpu_limit,
        memory_limit_bytes=memory_limit_bytes,
        env=env,
        ports=ports,
        labels=labels,
        workspace_host_path=workspace_host_path,
        existing_container_id=existing_container_id,
    )
    if not ensure_res.container_id:
        raise ContainerCreateError("ensure_container returned an empty container_id")

    start_res = runtime.start_container(container_id=ensure_res.container_id)
    if not start_res.success:
        raise ContainerStartError(start_res.message or "container did not reach running state")

    inspected = runtime.inspect_container(container_id=ensure_res.container_id)
    netns = runtime.get_container_netns_ref(container_id=ensure_res.container_id)

    container_id = inspected.container_id or ensure_res.container_id
    ports_out = inspected.ports if inspected.ports else ensure_res.resolved_ports

    return EnsureRunningRuntimeResult(
        container_id=container_id,
        container_state=inspected.container_state,
        pid=netns.pid,
        netns_ref=netns.netns_ref,
        ports=ports_out,
        node_id=ensure_res.node_id,
    )
