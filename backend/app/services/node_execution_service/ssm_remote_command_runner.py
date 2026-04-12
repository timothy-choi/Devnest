"""Run argv lists on an EC2 instance via SSM (topology / ``docker`` CLI)."""

from __future__ import annotations

import shlex

from botocore.client import BaseClient

from app.libs.topology.system.command_runner import CommandRunner

from .errors import SsmExecutionError
from .ssm_send_command import build_ssm_client, send_run_shell_script


class SsmRemoteCommandRunner(CommandRunner):
    """
    Executes ``cmd`` on ``instance_id`` through ``AWS-RunShellScript``.

    Mirrors :class:`~app.services.node_execution_service.ssh_command_runner.SshRemoteCommandRunner`
    for topology and probe commands without SSH keys.
    """

    def __init__(
        self,
        *,
        instance_id: str,
        region: str,
        ssm_client: BaseClient | None = None,
    ) -> None:
        self._instance_id = (instance_id or "").strip()
        self._region = (region or "").strip()
        self._ssm = ssm_client or build_ssm_client(region=self._region or None)

    def run(self, cmd: list[str]) -> str:
        if not cmd:
            raise ValueError("cmd must be a non-empty list of strings")
        inner = shlex.join(str(x) for x in cmd)
        script = f"set -euo pipefail && {inner}"
        try:
            stdout, _stderr = send_run_shell_script(
                self._ssm,
                self._instance_id,
                [script],
                comment="DevNest-runner",
            )
        except SsmExecutionError as e:
            pretty = shlex.join(str(x) for x in cmd)
            raise RuntimeError(f"ssm remote command failed: {pretty}\n{e}") from e
        return stdout or ""
