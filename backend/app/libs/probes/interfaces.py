"""Abstract probe runner: verification-only, read-only; no repair or reconcile."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .results import (
    ContainerProbeResult,
    ServiceProbeResult,
    TopologyProbeResult,
    WorkspaceHealthResult,
)


class ProbeRunner(ABC):
    """
    Read-only workspace and dependency checks.

    This package does not import ``runtime`` or ``topology``; concrete runners receive
    collaborators via ``__init__``. Callers must not use this layer to mutate containers,
    bridges, or database rows.

    String ids (``topology_id``, ``workspace_id``) are API-shaped; implementations parse
    them to the types expected by persistence when calling adapters.
    """

    @abstractmethod
    def check_container_running(
        self,
        *,
        container_id: str,
    ) -> ContainerProbeResult:
        """Verify the container exists and is in a running state (runtime inspection only)."""
        ...

    @abstractmethod
    def check_topology_state(
        self,
        *,
        topology_id: str,
        node_id: str,
        workspace_id: str,
        expected_port: int = 8080,
    ) -> TopologyProbeResult:
        """
        Verify topology runtime and attachment health for this workspace on ``node_id``.

        ``expected_port`` is the in-container service port used to build ``internal_endpoint``
        (e.g. ``{workspace_ip}:{expected_port}``) when attachment data is healthy.
        """
        ...

    @abstractmethod
    def check_service_reachable(
        self,
        *,
        workspace_ip: str,
        port: int = 8080,
        timeout_seconds: float = 2.0,
    ) -> ServiceProbeResult:
        """Verify TCP (or HTTP) reachability to ``workspace_ip:port`` within ``timeout_seconds``."""
        ...

    @abstractmethod
    def check_workspace_health(
        self,
        *,
        workspace_id: str,
        topology_id: str,
        node_id: str,
        container_id: str,
        expected_port: int = 8080,
        timeout_seconds: float = 2.0,
    ) -> WorkspaceHealthResult:
        """
        Run container, topology, and service checks and return a single roll-up.

        Implementations compose the granular methods; no repair or reconcile.
        """
        ...
