"""Extension points for node execution (V1 bundle; future SSM / node agent).

V1 resolves a concrete backend via :func:`resolve_node_execution_bundle`, which returns
:class:`NodeExecutionBundle` — a :class:`NodeExecutionBackend` implementation.

Later phases can add factories that build the same shape over AWS SSM, a sidecar agent, or
gRPC without changing orchestrator wiring.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

import docker

from app.libs.topology.system.command_runner import CommandRunner

_EnsureProjectDir = Callable[[str, str], str]


@runtime_checkable
class NodeExecutionBackend(Protocol):
    """
    Dependencies the orchestrator needs to run workspace workloads on one execution host.

    - **docker_client** — Docker API for that host (local socket or ``ssh://`` per docker-py).
    - **topology_command_runner** — Linux ``ip``/``nsenter``/bridge commands on the **same** host as the daemon.
    - **service_reachability_runner** — optional; when set, IDE TCP probes run from that host
      (needed when the worker cannot reach workspace internal IPs).
    """

    docker_client: docker.DockerClient
    topology_command_runner: CommandRunner
    service_reachability_runner: CommandRunner | None

    def ensure_workspace_project_dir(self, projects_base: str, workspace_id: str) -> str:
        """Return absolute project path on the execution host, creating directories if needed."""
        ...
