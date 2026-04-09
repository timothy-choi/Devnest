"""Unit tests for ``bridge_ops`` parsing (no host ``ip``)."""

from __future__ import annotations

from app.libs.topology.system.bridge_ops import check_bridge_link_up
from app.libs.topology.system.command_runner import CommandRunner


def test_check_bridge_link_up_oper_state_up() -> None:
    class R:
        def run(self, cmd: list[str]) -> str:
            return "3: br0: mtu 1500 state UP\n"

    assert check_bridge_link_up("br0", runner=R()) is True


def test_check_bridge_link_up_admin_up_no_carrier() -> None:
    """Empty bridges often keep MULTICAST,UP flags while oper state is DOWN (NO-CARRIER)."""

    class R:
        def run(self, cmd: list[str]) -> str:
            return (
                "3: brx: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 "
                "qdisc noqueue state DOWN mode DEFAULT group default\n"
            )

    assert check_bridge_link_up("brx", runner=R()) is True


def test_check_bridge_link_up_admin_down() -> None:
    class R:
        def run(self, cmd: list[str]) -> str:
            return "3: brz: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN\n"

    assert check_bridge_link_up("brz", runner=R()) is False
