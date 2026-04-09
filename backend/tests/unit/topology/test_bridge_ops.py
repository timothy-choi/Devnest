"""Unit tests for ``bridge_ops`` parsing (no host ``ip``)."""

from __future__ import annotations

from app.libs.topology.system.bridge_ops import check_bridge_link_up, ensure_bridge_address


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


def test_ensure_bridge_address_skips_add_when_ip_already_present() -> None:
    cmds: list[list[str]] = []

    class R:
        def run(self, cmd: list[str]) -> str:
            cmds.append(list(cmd))
            if len(cmd) >= 4 and cmd[0:4] == ["ip", "link", "show", "dev"]:
                return "1: br-idem: <UP> state UP\n"
            if len(cmd) >= 6 and cmd[0:6] == ["ip", "-o", "-4", "addr", "show", "dev"]:
                return "3: br-idem    inet 10.20.1.1/24 scope global br-idem\n"
            raise AssertionError(f"unexpected: {cmd}")

    ensure_bridge_address("br-idem", "10.20.1.1", "10.20.1.0/24", runner=R())
    assert not any(len(c) >= 3 and c[1:3] == ["addr", "add"] for c in cmds)


def test_ensure_bridge_address_swallows_add_failure_if_addr_now_visible() -> None:
    """Second ensure_* sync: add may fail with non-'File exists' text; addr check confirms success."""

    class R:
        def __init__(self) -> None:
            self._phase = 0

        def run(self, cmd: list[str]) -> str:
            if len(cmd) >= 4 and cmd[0:4] == ["ip", "link", "show", "dev"]:
                return "1: br-rec: <UP> state UP\n"
            if len(cmd) >= 6 and cmd[0:6] == ["ip", "-o", "-4", "addr", "show", "dev"]:
                self._phase += 1
                if self._phase == 1:
                    return ""
                return "3: br-rec    inet 10.20.2.1/24 scope global br-rec\n"
            if len(cmd) >= 4 and cmd[1:3] == ["addr", "add"]:
                raise RuntimeError("RTNETLINK answers: weird duplicate message")
            raise AssertionError(f"unexpected: {cmd}")

    ensure_bridge_address("br-rec", "10.20.2.1", "10.20.2.0/24", runner=R())
