"""Unit tests: :class:`SshRemoteCommandRunner`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.node_execution_service.ssh_command_runner import SshRemoteCommandRunner


@patch("app.libs.topology.system.command_runner.subprocess.run")
def test_ssh_remote_command_runner_prefixes_ssh(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="out\n", returncode=0)
    r = SshRemoteCommandRunner(ssh_user="ubuntu", ssh_host="10.0.0.5", ssh_port=2222)
    out = r.run(["ip", "link", "show"])
    assert out == "out\n"
    cmd = mock_run.call_args[0][0]
    assert cmd[:11] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        "2222",
        "ubuntu@10.0.0.5",
        "--",
    ]
    assert cmd[-3:] == ["ip", "link", "show"]
