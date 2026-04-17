"""Unit tests: :class:`HostPid1NsenterRunner`."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.libs.topology.system.host_nsenter_command_runner import HostPid1NsenterRunner


def test_prefixes_ip_with_nsenter_pid1() -> None:
    inner = MagicMock()
    inner.run.return_value = "ok"
    r = HostPid1NsenterRunner(inner)
    out = r.run(["ip", "link", "show", "dev", "eth0"])
    assert out == "ok"
    inner.run.assert_called_once_with(
        ["nsenter", "-t", "1", "-m", "-n", "-p", "--", "ip", "link", "show", "dev", "eth0"],
    )


def test_passes_through_nsenter_commands() -> None:
    inner = MagicMock()
    inner.run.return_value = ""
    r = HostPid1NsenterRunner(inner)
    cmd = ["nsenter", "-n", "-t", "42", "--", "ip", "addr", "show"]
    r.run(cmd)
    inner.run.assert_called_once_with(cmd)
