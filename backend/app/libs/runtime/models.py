"""Normalized result types returned by the Runtime Adapter (no raw Docker SDK objects)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeEnsureResult:
    """
    Outcome of ``ensure_container``: idempotently align runtime with the desired spec.

    Fields:
        container_id: Engine container id (may be empty only on abnormal adapter outcomes).
        exists: Whether the container record exists after ensure.
        created_new: Whether a new container was created in this call (vs reused).
        container_state: Normalized lifecycle state (e.g. created, running, exited).
        resolved_ports: Published ``(host_port, container_port)`` pairs as observed or intended.
        node_id: Execution node when applicable (e.g. Swarm); ``None`` for single-host Docker.
    """

    container_id: str
    exists: bool
    created_new: bool
    container_state: str
    resolved_ports: tuple[tuple[int, int], ...]
    node_id: str | None = None


@dataclass(frozen=True)
class RuntimeActionResult:
    """
    Outcome of a mutating action: ``start_container``, ``stop_container``, ``restart_container``,
    or ``delete_container``.

    Fields:
        container_id: Target container id (or name) as returned by the adapter.
        container_state: Observed state after the action (or best-effort snapshot).
        success: Whether the adapter considers the action to have succeeded.
        message: Optional human-readable detail (often used when ``success`` is false).
    """

    container_id: str
    container_state: str
    success: bool
    message: str | None = None


@dataclass(frozen=True)
class ContainerInspectionResult:
    """
    Normalized snapshot from ``inspect_container``.

    When ``exists`` is false, ``container_id`` is typically ``None`` and other fields reflect
    a missing container.

    Fields:
        exists: Whether the container exists in the runtime.
        container_id: Engine id when present.
        container_state: Normalized lifecycle state, or ``missing`` when not exists.
        pid: Host-visible init PID when running and reported by the engine; else ``None``.
        ports: Published ``(host_port, container_port)`` pairs.
        mounts: Normalized ``source:destination`` strings (or destination-only when no source).
        health_status: Adapter-normalized health (e.g. healthy, unhealthy); ``None`` if N/A.
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
    Resolved network namespace metadata from ``get_container_netns_ref``.

    Only returned on success; failures are signaled via ``NetnsRefError``.

    Fields:
        container_id: Container this reference belongs to.
        pid: Init process PID in the host PID namespace.
        netns_ref: Path to the ``net`` namespace (e.g. ``/proc/<pid>/ns/net``).
    """

    container_id: str
    pid: int
    netns_ref: str


@dataclass(frozen=True)
class EnsureRunningRuntimeResult:
    """
    Outcome of ``ensure_running_runtime_only``: ensured, started, inspected, netns resolved.

    Orchestrator helper only; not part of the ``RuntimeAdapter`` ABC.

    Fields:
        container_id: Canonical container id after inspect.
        container_state: Observed state (expected ``running`` on success).
        pid: Host PID used for the netns path.
        netns_ref: Path to the ``net`` namespace.
        ports: Published ``(host_port, container_port)`` pairs from inspection (or fallback).
        node_id: Propagated from ``RuntimeEnsureResult.node_id``.
    """

    container_id: str
    container_state: str
    pid: int
    netns_ref: str
    ports: tuple[tuple[int, int], ...]
    node_id: str | None
