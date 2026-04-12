"""Extension points for node execution (V1 bundle: local Docker, SSH Docker, SSM Docker).

:class:`NodeExecutionBundle` is built by :func:`resolve_node_execution_bundle` and wired into the
orchestrator via :mod:`app.services.orchestrator_service.app_factory`.

Future phases may add gRPC/agents while reusing the same bundle shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

import docker

from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.system.command_runner import CommandRunner

_EnsureProjectDir = Callable[[str, str], str]


@runtime_checkable
class NodeExecutionBackend(Protocol):
    """
    Dependencies the orchestrator needs to run workspace workloads on one execution host.

    - **docker_client** — Docker API for that host (local socket or ``ssh://`` per docker-py); ``None`` when using ``runtime_adapter``.
    - **runtime_adapter** — optional :class:`~app.libs.runtime.interfaces.RuntimeAdapter` (e.g. SSM docker CLI); when set, ``docker_client`` is ``None``.
    - **topology_command_runner** — Linux ``ip``/``nsenter``/bridge commands on the **same** host as the daemon.
    - **service_reachability_runner** — optional; when set, IDE TCP probes run from that host
      (needed when the worker cannot reach workspace internal IPs).

    Mode ``local_docker`` uses a **local** client; ``ssh_docker`` uses docker-py over SSH; ``ssm_docker``
    uses SSM Run Command for ``docker`` and host tools on the instance.
    """

    docker_client: docker.DockerClient | None
    runtime_adapter: RuntimeAdapter | None
    topology_command_runner: CommandRunner
    service_reachability_runner: CommandRunner | None

    def ensure_workspace_project_dir(self, projects_base: str, workspace_id: str) -> str:
        """Return absolute project path on the execution host, creating directories if needed."""
        ...
