"""
High-level SSM execution facade for EC2-backed nodes (shell + ``docker`` helpers).

Orchestrator/runtime code should prefer :class:`~app.libs.runtime.ssm_docker_runtime.SsmDockerRuntimeAdapter`
and :class:`SsmRemoteCommandRunner` for consistency. This module exposes explicit operations for
operators, tests, and future admin tooling.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

from botocore.client import BaseClient

from app.services.placement_service.models import ExecutionNode

from .errors import SsmExecutionError
from .ssm_send_command import build_ssm_client, send_run_shell_script


class SsmExecutionProvider:
    """Run shell on ``ExecutionNode`` rows that have ``provider_instance_id`` (EC2 id for SSM)."""

    def __init__(
        self,
        node: ExecutionNode,
        *,
        region: str | None = None,
        ssm_client: BaseClient | None = None,
    ) -> None:
        iid = (node.provider_instance_id or "").strip()
        if not iid:
            raise SsmExecutionError(
                f"node {node.node_key!r} has no provider_instance_id (required for SSM targeting)",
            )
        self._instance_id = iid
        r = (region or node.region or "").strip()
        self._region = r
        self._ssm = ssm_client or build_ssm_client(region=r or None)

    def execute_command(self, *lines: str) -> tuple[str, str]:
        """Run multi-line shell script; returns ``(stdout, stderr)`` on success."""
        cmds = [str(x) for x in lines if str(x).strip()]
        if not cmds:
            raise SsmExecutionError("execute_command requires at least one non-empty line")
        return send_run_shell_script(self._ssm, self._instance_id, cmds, comment="DevNest-exec")

    def _docker_argv(self, parts: Sequence[str]) -> list[str]:
        return ["docker", *[str(p) for p in parts]]

    def run_docker_argv(self, parts: Sequence[str]) -> str:
        """``docker <parts...>`` with ``set -e`` semantics."""
        argv = self._docker_argv(parts)
        inner = shlex.join(argv)
        out, _ = self.execute_command(f"set -euo pipefail && {inner}")
        return out

    def inspect_container(self, container_ref: str) -> str:
        """``docker inspect`` JSON array stdout."""
        ref = (container_ref or "").strip()
        return self.run_docker_argv(["inspect", ref])

    def start_container(self, container_ref: str) -> str:
        ref = (container_ref or "").strip()
        return self.run_docker_argv(["start", ref])

    def stop_container(self, container_ref: str, *, timeout_s: int = 10) -> str:
        ref = (container_ref or "").strip()
        return self.run_docker_argv(["stop", "-t", str(timeout_s), ref])

    def delete_container(self, container_ref: str, *, force: bool = True) -> str:
        """``docker rm``; uses ``-f`` by default so remove works on running containers."""
        ref = (container_ref or "").strip()
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(ref)
        return self.run_docker_argv(args)
