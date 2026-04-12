"""Resolved execution dependencies for one workspace job (Docker + host commands)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import docker

from app.libs.topology.system.command_runner import CommandRunner

# Satisfies :class:`NodeExecutionBackend` for static typing; no runtime registration required.


@dataclass
class NodeExecutionBundle:
    """
    Per-job wiring for runtime (Docker SDK), topology Linux commands, and workspace bind paths.

    ``service_reachability_runner`` when set causes :class:`~app.libs.probes.probe_runner.DefaultProbeRunner`
    to verify IDE TCP reachability **from the execution host** (required when the worker cannot route
    to workspace internal IPs).
    """

    docker_client: docker.DockerClient
    topology_command_runner: CommandRunner
    service_reachability_runner: CommandRunner | None
    _ensure_project_dir: Callable[[str, str], str]

    def ensure_workspace_project_dir(self, projects_base: str, workspace_id: str) -> str:
        return self._ensure_project_dir(projects_base, workspace_id)
