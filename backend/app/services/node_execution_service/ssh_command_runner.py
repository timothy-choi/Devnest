"""Run Linux commands on a remote host via OpenSSH (topology + remote mkdir)."""

from __future__ import annotations

import subprocess
from typing import Any

from app.libs.topology.system.command_runner import CommandRunner


class SshRemoteCommandRunner(CommandRunner):
    """
    Executes ``cmd`` on ``ssh_user@ssh_host`` using non-interactive SSH.

    Uses ``BatchMode=yes`` so missing keys fail fast instead of prompting.
    ``StrictHostKeyChecking=accept-new`` is a dev-friendly default; production should pin host keys
    or use a bastion (TODO).

    Topology and orchestrator code pass argv lists (e.g. ``ip``, ``nsenter``); avoid shell=True.
    """

    def __init__(
        self,
        *,
        ssh_user: str,
        ssh_host: str,
        ssh_port: int = 22,
    ) -> None:
        self._ssh_user = (ssh_user or "").strip()
        self._ssh_host = (ssh_host or "").strip()
        self._ssh_port = int(ssh_port)

    def run(self, cmd: list[str]) -> str:
        if not cmd:
            raise ValueError("cmd must be a non-empty list of strings")
        remote = f"{self._ssh_user}@{self._ssh_host}"
        prefixed: list[str] = self._ssh_prefix_argv(remote) + [str(x) for x in cmd]
        return super().run(prefixed)

    def _ssh_prefix_argv(self, remote_user_host: str) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(self._ssh_port),
            remote_user_host,
            "--",
        ]

    def popen_for_remote_command(self, remote_argv: list[str], **popen_kwargs: Any) -> subprocess.Popen:
        """Run ``remote_argv`` on the SSH host via ``subprocess.Popen`` (for binary tar streams).

        ``remote_argv`` is executed as a single remote argv list after ``ssh … --`` (no shell).
        Pass ``["sh", "-c", "..."]`` when shell features are required.
        """
        if not remote_argv:
            raise ValueError("remote_argv must be a non-empty list of strings")
        remote = f"{self._ssh_user}@{self._ssh_host}"
        prefixed = self._ssh_prefix_argv(remote) + [str(x) for x in remote_argv]
        return subprocess.Popen(prefixed, **popen_kwargs)
