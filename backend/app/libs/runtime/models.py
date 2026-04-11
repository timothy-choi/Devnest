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

    Maps an absolute host directory ``host_path`` into the container at
    ``WORKSPACE_PROJECT_CONTAINER_PATH`` (``/home/coder/project``). This mount is how IDE/terminal
    user files persist on the Docker host. Richer storage specs can extend the adapter later without
    changing this type.
    """

    host_path: str
    read_only: bool = False


# --- Optional code-server persistence (in addition to the required project mount) -------------
#
# code-server uses XDG-style dirs under ``/home/coder``. The adapter does **not** auto-mount these;
# pass ``WorkspaceExtraBindMountSpec`` entries in ``ensure_container(..., extra_bind_mounts=...)``.
#
# - ``CODE_SERVER_CONFIG_CONTAINER_PATH``: server config (e.g. ``config.yaml``, ``auth``). Does not
#   replace secrets management—set host directory permissions appropriately.
# - ``CODE_SERVER_DATA_CONTAINER_PATH``: user data dir where upstream layouts place **extensions**,
#   **workspace storage**, **User/** / **globalStorage** (editor-related state), etc. One bind here
#   covers “extensions + editor state” for typical code-server; no separate adapter constant per
#   subfolder to avoid over-fitting layout details.
#
# For any other absolute in-container path, pass ``container_path`` explicitly (e.g. future image
# changes). See ``CODE_SERVER_OPTIONAL_PERSISTENCE_CONTAINER_PATHS`` for the conventional pair.
CODE_SERVER_CONFIG_CONTAINER_PATH = "/home/coder/.config/code-server"
CODE_SERVER_DATA_CONTAINER_PATH = "/home/coder/.local/share/code-server"

CODE_SERVER_OPTIONAL_PERSISTENCE_CONTAINER_PATHS: tuple[str, ...] = (
    CODE_SERVER_CONFIG_CONTAINER_PATH,
    CODE_SERVER_DATA_CONTAINER_PATH,
)


@dataclass(frozen=True)
class WorkspaceExtraBindMountSpec:
    """
    Optional extra bind mount for code-server (or similar) persistence beside the project mount.

    Each entry maps an absolute host directory to an absolute ``container_path``. Typical
    ``container_path`` values are ``CODE_SERVER_CONFIG_CONTAINER_PATH`` and/or
    ``CODE_SERVER_DATA_CONTAINER_PATH`` (extensions + editor/workspace state under the latter—see
    module comments). Omitted entries mean that subtree is ephemeral inside the container. The
    adapter never reads or injects secrets; avoid world-readable host dirs for sensitive config.
    """

    host_path: str
    container_path: str
    read_only: bool = False


@dataclass(frozen=True)
class BindMountInfo:
    """
    Normalized bind mount from engine inspection (``Type: bind`` only).

    ``propagation`` mirrors Docker's mount propagation when present (e.g. ``rprivate``); ``None``
    if the engine omitted it—useful for debugging bind behavior alongside ``host_path`` /
    ``container_path``.
    """

    host_path: str
    container_path: str
    read_only: bool
    propagation: str | None = None


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
            host publish is configured, or before the engine has assigned ephemeral host ports).
            On reuse, matches ``inspect_container`` when the engine reports bindings; after create,
            prefer re-inspecting once running to read ephemeral assignments.
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
        container_id: Canonical engine id when the adapter observed it (e.g. from inspect);
            otherwise the caller's ``container_id`` argument (including for idempotent
            ``stop_container`` / ``delete_container`` when the container was already missing).
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
        ports: Host-to-container published ``(host_port, container_port)`` TCP pairs from the engine;
            empty when nothing is published to the host (each workspace can still use
            ``WORKSPACE_IDE_CONTAINER_PORT`` inside its own network namespace).
        mounts: Normalized ``source:destination`` strings (or destination-only when no source).
        bind_mounts: Structured bind mounts only (``Type: bind`` from the engine), in engine order.
        workspace_project_mount: Convenience pointer to the bind whose ``container_path`` matches
            ``WORKSPACE_PROJECT_CONTAINER_PATH`` (normalize trailing slashes); ``None`` if missing.
            For full debugging use ``bind_mounts`` and ``mounts`` together.
        health_status: Adapter-normalized health (e.g. healthy, unhealthy); ``None`` if N/A.
        labels: Engine ``Config.Labels`` as sorted ``(key, value)`` pairs (immutable snapshot).
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
    labels: tuple[tuple[str, str], ...] = ()


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
