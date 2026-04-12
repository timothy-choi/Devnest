"""Resolved execution dependencies for one workspace job (Docker + host commands)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import docker

from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.system.command_runner import CommandRunner

# Satisfies :class:`NodeExecutionBackend` for static typing; no runtime registration required.


@dataclass
class NodeExecutionBundle:
    """
    Per-job wiring for runtime (Docker SDK and/or SSM CLI), topology Linux commands, and paths.

    Exactly one of ``docker_client`` or ``runtime_adapter`` must be non-``None``. Local/SSH modes
    use :class:`~app.libs.runtime.docker_runtime.DockerRuntimeAdapter` via ``docker_client``;
    ``ssm_docker`` sets ``runtime_adapter`` to :class:`~app.libs.runtime.ssm_docker_runtime.SsmDockerRuntimeAdapter`.

    ``service_reachability_runner`` when set causes :class:`~app.libs.probes.probe_runner.DefaultProbeRunner`
    to verify IDE TCP reachability **from the execution host**.
    """

    docker_client: docker.DockerClient | None
    topology_command_runner: CommandRunner
    service_reachability_runner: CommandRunner | None
    _ensure_project_dir: Callable[[str, str], str]
    runtime_adapter: RuntimeAdapter | None = None

    def ensure_workspace_project_dir(self, projects_base: str, workspace_id: str) -> str:
        return self._ensure_project_dir(projects_base, workspace_id)
