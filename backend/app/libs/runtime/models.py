"""Normalized result types returned by the Runtime Adapter (no raw Docker SDK objects)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeEnsureResult:
    """
    Outcome of EnsureContainer: idempotently align runtime with desired spec.

    ``resolved_ports`` is ``(host_port, container_port)`` pairs after ensure (publish/bindings).
    ``node_id`` is the execution node when applicable (e.g. Swarm); otherwise ``None``.
    """

    container_id: str
    exists: bool
    created_new: bool
    container_state: str
    resolved_ports: tuple[tuple[int, int], ...]
    node_id: str | None = None


@dataclass(frozen=True)
class RuntimeActionResult:
    """Outcome of a mutating container action (start/stop/restart/delete)."""

    container_id: str
    container_state: str
    success: bool
    message: str | None = None


@dataclass(frozen=True)
class ContainerInspectionResult:
    """
    Snapshot from InspectContainer: existence, identity, and observable runtime shape.

    ``ports`` uses the same ``(host_port, container_port)`` convention as ``RuntimeEnsureResult``.
    ``mounts`` are normalized destination paths (or source→dest strings) as plain strings.
    ``health_status`` is adapter-normalized (e.g. healthy/unhealthy/starting/unknown), not raw engine text.
    """

    exists: bool
    container_id: str | None
    container_state: str
    pid: int | None
    ports: tuple[tuple[int, int], ...]
    mounts: tuple[str, ...]
    health_status: str | None = None


@dataclass(frozen=True)
class NetnsRefResult:
    """
    Network namespace reference for future topology attach (e.g. ``/proc/<pid>/ns/net``).

    ``pid`` is the container’s init process id in the host pid namespace when available.
    """

    container_id: str
    pid: int | None
    netns_ref: str | None


@dataclass(frozen=True)
class EnsureRunningRuntimeResult:
    """
    Outcome of ``ensure_running_runtime_only``: container ensured, started, inspected, netns resolved.

    Built for internal callers (no HTTP). ``pid`` and ``netns_ref`` are set when the runtime reports
    a host PID (typically while the container is running).
    """

    container_id: str
    container_state: str
    pid: int
    netns_ref: str
    ports: tuple[tuple[int, int], ...]
    node_id: str | None
