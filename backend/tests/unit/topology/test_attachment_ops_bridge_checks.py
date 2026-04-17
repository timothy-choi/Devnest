"""Unit tests: bridge slave listing + workspace iface IPv4 helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.libs.topology.system.attachment_ops import (
    check_bridge_master_list_contains_if,
    check_workspace_ipv4_assigned_on_iface,
)


def test_check_bridge_master_list_contains_if_positive() -> None:
    r = MagicMock()
    r.run.return_value = "4: vhtest01: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 master br0 state UP\n"
    assert check_bridge_master_list_contains_if("vhtest01", "br0", runner=r) is True


def test_check_bridge_master_list_contains_if_negative() -> None:
    r = MagicMock()
    r.run.return_value = "4: other0: <BROADCAST> mtu 1500 master br0 state UP\n"
    assert check_bridge_master_list_contains_if("vhtest01", "br0", runner=r) is False


def test_check_workspace_ipv4_assigned_on_iface_positive() -> None:
    r = MagicMock()
    r.run.return_value = "eth1            UP             10.128.0.11/20 \n"
    ok = check_workspace_ipv4_assigned_on_iface(
        "1",
        "eth1",
        "10.128.0.11",
        "10.128.0.0/20",
        runner=r,
    )
    assert ok is True
    assert r.run.called


def test_check_workspace_ipv4_assigned_on_iface_wrong_ip() -> None:
    r = MagicMock()
    r.run.return_value = "eth1            UP             10.128.0.12/20 \n"
    ok = check_workspace_ipv4_assigned_on_iface(
        "1",
        "eth1",
        "10.128.0.11",
        "10.128.0.0/20",
        runner=r,
    )
    assert ok is False
