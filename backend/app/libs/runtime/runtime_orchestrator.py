"""Internal runtime-only orchestration: sequence ``RuntimeAdapter`` calls (no DB, topology, or HTTP)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import ContainerCreateError, ContainerStartError
from .interfaces import RuntimeAdapter
from .models import (
    EnsureRunningRuntimeResult,
    WorkspaceExtraBindMountSpec,
    WorkspaceProjectMountSpec,
)


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
    project_mount: WorkspaceProjectMountSpec | None = None,
    workspace_host_path: str | None = None,
    extra_bind_mounts: Sequence[WorkspaceExtraBindMountSpec] | None = None,
    existing_container_id: str | None = None,
) -> EnsureRunningRuntimeResult:
    """
    Narrow orchestrator slice: bring a container to a running state and collect runtime facts.

    **Call order:** ``ensure_container`` → ``start_container`` → ``inspect_container`` →
    ``get_container_netns_ref`` (all via ``runtime``).

    **Raises:** ``ContainerCreateError`` (empty id after ensure), ``ContainerStartError`` (start
    not successful), ``NetnsRefError`` (from ``get_container_netns_ref`` if PID/netns unavailable).

    Does not write the database, attach topology, compute public URLs, or register gateway routes.
    Callers persist workspace / routing state separately.
    """
    ensure_res = runtime.ensure_container(
        name=name,
        image=image,
        cpu_limit=cpu_limit,
        memory_limit_bytes=memory_limit_bytes,
        env=env,
        ports=ports,
        labels=labels,
        project_mount=project_mount,
        workspace_host_path=workspace_host_path,
        extra_bind_mounts=extra_bind_mounts,
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
    resolved_ports = inspected.ports if inspected.ports else ensure_res.resolved_ports

    return EnsureRunningRuntimeResult(
        container_id=container_id,
        container_state=inspected.container_state,
        pid=netns.pid,
        netns_ref=netns.netns_ref,
        resolved_ports=resolved_ports,
        node_id=ensure_res.node_id,
        workspace_ide_container_port=ensure_res.workspace_ide_container_port,
        workspace_project_mount=inspected.workspace_project_mount or ensure_res.workspace_project_mount,
    )
