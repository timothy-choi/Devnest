"""Unit tests: :class:`HostPid1NsenterRunner`."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.libs.topology.system.host_nsenter_command_runner import (
    HostPid1NsenterProbeRunner,
    HostPid1NsenterRunner,
)


def test_prefixes_ip_with_nsenter_pid1() -> None:
    inner = MagicMock()
    inner.run.return_value = "ok"
    r = HostPid1NsenterRunner(inner)
    out = r.run(["ip", "link", "show", "dev", "eth0"])
    assert out == "ok"
    inner.run.assert_called_once_with(
        ["nsenter", "-t", "1", "-n", "--", "ip", "link", "show", "dev", "eth0"],
    )


def test_passes_through_nsenter_commands() -> None:
    inner = MagicMock()
    inner.run.return_value = ""
    r = HostPid1NsenterRunner(inner)
    cmd = ["nsenter", "-n", "-t", "42", "--", "ip", "addr", "show"]
    r.run(cmd)
    inner.run.assert_called_once_with(cmd)


def test_probe_runner_prefixes_nc_with_nsenter_pid1() -> None:
    inner = MagicMock()
    inner.run.return_value = ""
    r = HostPid1NsenterProbeRunner(inner)
    r.run(["timeout", "2", "nc", "-z", "10.0.0.1", "8080"])
    inner.run.assert_called_once_with(
        ["nsenter", "-t", "1", "-n", "--", "timeout", "2", "nc", "-z", "10.0.0.1", "8080"],
    )


def test_probe_runner_passes_through_nsenter() -> None:
    inner = MagicMock()
    inner.run.return_value = "lo UP"
    r = HostPid1NsenterProbeRunner(inner)
    cmd = ["nsenter", "-n", "-t", "99", "--", "ip", "addr"]
    r.run(cmd)
    inner.run.assert_called_once_with(cmd)
