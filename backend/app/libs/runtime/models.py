"""Normalized result types returned by the Runtime Adapter (no raw Docker SDK objects)."""

from __future__ import annotations

from dataclasses import dataclass

# Canonical in-container path for persisted workspace project files (IDE / terminal).
WORKSPACE_PROJECT_CONTAINER_PATH = "/home/coder/project"
# code-server / workspace IDE listens on this port inside the container (host publish is optional).
WORKSPACE_IDE_CONTAINER_PORT = 8080


@dataclass(frozen=True)
class WorkspaceProjectMountSpec:
    """
    Storage input for the primary workspace project bind mount.

    Maps host directory ``host_path`` into the container at ``WORKSPACE_PROJECT_CONTAINER_PATH``.
    This is the first required mount for user-generated project files; richer storage specs can
    extend the adapter later without changing this type.
    """

    host_path: str
    read_only: bool = False


# Typical code-server persistence locations under ``/home/coder`` (use with ``WorkspaceExtraBindMountSpec``).
# Add more module-level constants here if you need additional fixed targets; the adapter accepts any
# absolute ``container_path`` string.
CODE_SERVER_CONFIG_CONTAINER_PATH = "/home/coder/.config/code-server"
CODE_SERVER_DATA_CONTAINER_PATH = "/home/coder/.local/share/code-server"


@dataclass(frozen=True)
class WorkspaceExtraBindMountSpec:
    """
    Optional extra bind mount for workspace-adjacent persistence (e.g. code-server config,
    extension data). Separate from the required project mount.

    Callers supply per-workspace ``host_path`` directories; ``container_path`` is usually one of
    ``CODE_SERVER_CONFIG_CONTAINER_PATH`` or ``CODE_SERVER_DATA_CONTAINER_PATH``. The adapter does
    not read secrets from the image—avoid mounting sensitive host dirs with loose permissions.
    """

    host_path: str
    container_path: str
    read_only: bool = False


@dataclass(frozen=True)
class BindMountInfo:
    """Normalized bind mount from engine inspection (``Type: bind`` only)."""

    host_path: str
    container_path: str
    read_only: bool


@dataclass(frozen=True)
class RuntimeEnsureResult:
    """
    Outcome of ``ensure_container``: idempotently align runtime with the desired spec.

    Fields:
        container_id: Engine container id (may be empty only on abnormal adapter outcomes).
        exists: Whether the container record exists after ensure.
        created_new: Whether a new container was created in this call (vs reused).
        container_state: Normalized lifecycle state (e.g. created, running, exited).
        resolved_ports: Host-published ``(host_port, container_port)`` pairs only (empty when no
            host publish is configured). On reuse, matches ``inspect_container`` when the engine
            reports bindings; with explicit ephemeral or pinned maps, filled after publish exists.
        node_id: Execution node when applicable (e.g. Swarm); ``None`` for single-host Docker.
        workspace_ide_container_port: In-container port for the workspace IDE (code-server);
            always ``WORKSPACE_IDE_CONTAINER_PORT`` for this adapter; independent of host publishing.
        workspace_project_mount: Bind mount for ``WORKSPACE_PROJECT_CONTAINER_PATH`` when the
            engine reports it; ``None`` if missing or not a bind (e.g. old container layout).
    """

    container_id: str
    exists: bool
    created_new: bool
    container_state: str
    resolved_ports: tuple[tuple[int, int], ...]
    node_id: str | None = None
    workspace_ide_container_port: int = WORKSPACE_IDE_CONTAINER_PORT
    workspace_project_mount: BindMountInfo | None = None


@dataclass(frozen=True)
class RuntimeActionResult:
    """
    Outcome of a mutating action: ``start_container``, ``stop_container``, ``restart_container``,
    or ``delete_container``.

    Fields:
        container_id: Engine id when known; otherwise the caller's ``container_id`` argument
            (e.g. ``stop_container`` / ``delete_container`` when the container is missing).
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
        bind_mounts: Structured bind mounts only (``Type: bind`` from the engine).
        workspace_project_mount: The bind whose destination is ``WORKSPACE_PROJECT_CONTAINER_PATH``,
            if present.
        health_status: Adapter-normalized health (e.g. healthy, unhealthy); ``None`` if N/A.
    """

    exists: bool
    container_id: str | None
    container_state: str
    pid: int | None
    ports: tuple[tuple[int, int], ...]
    mounts: tuple[str, ...]
    health_status: str | None = None
    bind_mounts: tuple[BindMountInfo, ...] = ()
    workspace_project_mount: BindMountInfo | None = None


@dataclass(frozen=True)
class NetnsRefResult:
    """
    Resolved network namespace metadata from ``get_container_netns_ref``.

    Only returned on success; failures are signaled via ``NetnsRefError``.

    Fields:
        container_id: Container this reference belongs to.
        pid: Init process PID in the host PID namespace.
        netns_ref: Path to the ``net`` namespace on the Docker host (Linux: ``/proc/<pid>/ns/net``).
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
        resolved_ports: Host-published ``(host_port, container_port)`` pairs from inspection (or
            fallback from ``RuntimeEnsureResult.resolved_ports``).
        node_id: Propagated from ``RuntimeEnsureResult.node_id``.
        workspace_ide_container_port: In-container IDE port from ``RuntimeEnsureResult``.
        workspace_project_mount: Project bind from inspect when available.
    """

    container_id: str
    container_state: str
    pid: int
    netns_ref: str
    resolved_ports: tuple[tuple[int, int], ...]
    node_id: str | None
    workspace_ide_container_port: int = WORKSPACE_IDE_CONTAINER_PORT
    workspace_project_mount: BindMountInfo | None = None
