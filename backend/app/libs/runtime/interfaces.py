"""Abstract runtime adapter: orchestrator-facing container lifecycle (no workspace semantics)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from .errors import (
    ContainerCreateError,
    ContainerDeleteError,
    ContainerNotFoundError,
    ContainerStartError,
    ContainerStopError,
    NetnsRefError,
)
from .models import (
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
    WorkspaceExtraBindMountSpec,
    WorkspaceProjectMountSpec,
)


class RuntimeAdapter(ABC):
    """
    Container lifecycle abstraction for the orchestrator.

    Implementations translate these calls to Docker/containerd/etc. and return only normalized
    dataclasses defined in ``models`` (no raw engine objects). Callers persist runtime rows;
    adapters do not write application database state.
    """

    @abstractmethod
    def ensure_container(
        self,
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
    ) -> RuntimeEnsureResult:
        """
        Idempotently ensure a container exists for the given name/spec.

        Returns:
            ``RuntimeEnsureResult`` with ``resolved_ports`` and ``container_state``.

        Raises:
            ContainerCreateError: Build/create/pull or invalid configuration failed.

        Reuse order (Docker): if ``existing_container_id`` is set and still exists, that
        container is reused; otherwise if ``name`` resolves to an existing container, reuse
        it; otherwise create a container named ``name``.

        ``ports`` entries are optional host-publish pairs ``(host_port, container_port)``.
        Omit ``ports`` (or pass an empty sequence) to create **without** publishing any container
        port to the host—multiple workspaces then do not consume host ports; the in-container
        IDE still listens on ``WORKSPACE_IDE_CONTAINER_PORT`` inside the container. Use a positive
        ``host_port`` to pin a host port, or ``host_port <= 0`` with an explicit pair such as
        ``(0, WORKSPACE_IDE_CONTAINER_PORT)`` to request an engine-assigned ephemeral host port.
        If the same ``container_port`` appears more than once, the last pair wins.

        ``image`` may be omitted; the Docker adapter uses ``DEVNEST_WORKSPACE_IMAGE`` (or its
        built-in default) for the workspace/code-server image in that case.

        **Project storage (required on create):** pass ``project_mount`` (preferred) and/or
        ``workspace_host_path`` (legacy alias for the same host directory). The Docker adapter
        bind-mounts that host path to ``WORKSPACE_PROJECT_CONTAINER_PATH`` (``/home/coder/project``).
        If both are set, they must match. ``project_mount.read_only`` selects ``:ro`` vs ``:rw``.

        **Optional code-server / state mounts:** ``extra_bind_mounts`` is a sequence of
        ``WorkspaceExtraBindMountSpec`` (host directory → absolute container path, e.g.
        ``CODE_SERVER_CONFIG_CONTAINER_PATH`` / ``CODE_SERVER_DATA_CONTAINER_PATH`` in ``models``).
        Omitted or empty means no extra binds. Must not duplicate the project mount destination.

        **Reuse note:** When an existing container is returned, create-only arguments above are
        ignored; ``RuntimeEnsureResult`` reflects the running container (ports, project bind from
        inspect). The in-container IDE port contract remains ``WORKSPACE_IDE_CONTAINER_PORT``.
        """

    @abstractmethod
    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Start a stopped container (inspect first; re-inspect after ``start``).

        Returns:
            ``RuntimeActionResult``. Already-running containers return ``success=True`` without
            invoking start (idempotent).

        Raises:
            ContainerNotFoundError: Inspect reports the container is missing.
            ContainerStartError: Engine API error while starting.
        """

    @abstractmethod
    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Stop a running container (inspect first; re-inspect after ``stop``).

        Returns:
            ``RuntimeActionResult``. Missing containers may return ``success=True`` with
            ``container_state`` set to ``missing`` (idempotent cleanup). Already-inactive states return
            ``success=True`` without calling the engine stop API.

        Raises:
            ContainerStopError: Engine API error while stopping.
        """

    @abstractmethod
    def restart_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Restart a container (inspect first; engine-native restart; re-inspect).

        Returns:
            ``RuntimeActionResult`` (``success`` reflects whether the final state is ``running``).

        Raises:
            ContainerNotFoundError: Inspect reports missing (or missing after restart).
            ContainerStartError: Engine API error during restart (Docker combines stop/start).
        """

    @abstractmethod
    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Remove a container (inspect first; re-inspect after removal).

        Returns:
            ``RuntimeActionResult``. Missing containers may return ``success=True`` with
            ``container_state`` set to ``missing`` (idempotent cleanup).

        Raises:
            ContainerStopError: Graceful stop failed before remove (when the container was active).
            ContainerDeleteError: Engine API error while removing.
        """

    @abstractmethod
    def inspect_container(self, *, container_id: str) -> ContainerInspectionResult:
        """
        Return a normalized inspection snapshot.

        Returns:
            ``ContainerInspectionResult`` with ``exists=False`` when the container is missing
            (no exception for “not found”).
        """

    @abstractmethod
    def get_container_netns_ref(self, *, container_id: str) -> NetnsRefResult:
        """
        Resolve host PID and ``net`` namespace path (inspect first; no topology side effects).

        Implementations typically derive ``netns_ref`` from ``/proc/<pid>/ns/net`` on Linux Docker
        hosts using the engine-reported init ``Pid`` (host PID namespace).

        Returns:
            ``NetnsRefResult`` with non-empty ``pid`` and ``netns_ref``.

        Raises:
            NetnsRefError: Container missing or no usable host PID for namespace resolution.
        """
