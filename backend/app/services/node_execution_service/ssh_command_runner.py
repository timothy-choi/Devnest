"""Run Linux commands on a remote host via OpenSSH (topology + remote mkdir)."""

from __future__ import annotations

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
        prefixed: list[str] = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(self._ssh_port),
            remote,
            "--",
        ] + [str(x) for x in cmd]
        return super().run(prefixed)
