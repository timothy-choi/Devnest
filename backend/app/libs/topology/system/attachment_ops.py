"""V1 workspace attachment: node-local veth + netns helpers (no DB, no NAT/proxy).

Uses ``ip`` and ``nsenter`` (util-linux). Intended for Linux hosts where the container runtime
exposes ``netns_ref`` as ``/proc/<pid>/ns/net`` (or a numeric PID string).

All execution goes through ``CommandRunner`` for mocking in tests.
"""

from __future__ import annotations

import ipaddress
import re

from .command_runner import CommandRunner


def _validate_ifname(name: str, *, label: str = "interface") -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{label} name must be a non-empty string")
    s = name.strip()
    if len(s) > 15:
        raise ValueError(f"{label} name too long for Linux IFNAMSIZ: {s!r} (len={len(s)})")
    if any(ch.isspace() for ch in s):
        raise ValueError(f"{label} name must not contain whitespace: {s!r}")
    return s


def _pid_from_netns_ref(netns_ref: str) -> str:
    """
    Return PID string for ``ip link set … netns <pid>`` and ``nsenter -n -t <pid>``.

    Accepts:
    - ``/proc/<pid>/ns/net``
    - decimal PID string (e.g. ``"12345"``)
    """
    if not isinstance(netns_ref, str) or not netns_ref.strip():
        raise ValueError("netns_ref must be a non-empty string")
    s = netns_ref.strip()
    if re.fullmatch(r"\d+", s):
        pid = int(s, 10)
        if pid <= 0:
            raise ValueError(f"invalid PID in netns_ref: {netns_ref!r}")
        return s
    m = re.match(r"^/proc/(\d+)/ns/net$", s)
    if not m:
        raise ValueError(
            f"unsupported netns_ref {netns_ref!r}; expected '/proc/<pid>/ns/net' or numeric PID",
        )
    return m.group(1)


def validate_netns_ref(netns_ref: str) -> str:
    """
    Return stripped ``netns_ref`` after validating V1 shape (PID or ``/proc/<pid>/ns/net``).

    Raises ``ValueError`` if empty or unsupported.
    """
    if not isinstance(netns_ref, str):
        raise ValueError("netns_ref must be a string")
    s = netns_ref.strip()
    if not s:
        raise ValueError("netns_ref is required for V1 attach")
    _pid_from_netns_ref(s)
    return s


def _netns_prefix(netns_ref: str) -> list[str]:
    """``nsenter`` argv prefix to run a command in the target network namespace."""
    pid = _pid_from_netns_ref(netns_ref)
    return ["nsenter", "-n", "-t", pid, "--"]


def check_host_veth_enslaved_to_bridge(
    host_if: str,
    bridge_name: str,
    *,
    runner: CommandRunner | None = None,
) -> bool:
    """
    Return True if ``host_if`` exists on the host and ``ip link`` reports ``master <bridge_name>``.

    Used to verify the host leg of a workspace veth is attached to the topology bridge.
    """
    h = _validate_ifname(host_if, label="host_if")
    br = _validate_ifname(bridge_name, label="bridge_name")
    r = runner or CommandRunner()
    try:
        out = r.run(["ip", "link", "show", "dev", h])
    except RuntimeError:
        return False
    return bool(re.search(rf"\bmaster\s+{re.escape(br)}\b", out))


def check_interface_exists(
    ifname: str,
    *,
    netns_ref: str | None = None,
    runner: CommandRunner | None = None,
) -> bool:
    """
    Return True if ``ifname`` exists on the host, or inside ``netns_ref`` when given.

    Host: ``ip link show dev <ifname>``. In netns: ``nsenter … -- ip link show dev <ifname>``.
    """
    ifn = _validate_ifname(ifname)
    r = runner or CommandRunner()
    try:
        if netns_ref is None:
            r.run(["ip", "link", "show", "dev", ifn])
        else:
            r.run([*_netns_prefix(netns_ref), "ip", "link", "show", "dev", ifn])
        return True
    except RuntimeError:
        return False


def create_veth_pair(host_if: str, container_if: str, *, runner: CommandRunner | None = None) -> None:
    """
    Create a veth pair: ``host_if`` on the host, ``container_if`` as peer (still on host until moved).

    Idempotent if both ends already exist.
    """
    h = _validate_ifname(host_if, label="host_if")
    c = _validate_ifname(container_if, label="container_if")
    if h == c:
        raise ValueError("host_if and container_if must differ")
    r = runner or CommandRunner()
    if check_interface_exists(h, runner=r) and check_interface_exists(c, runner=r):
        return
    try:
        r.run(["ip", "link", "add", h, "type", "veth", "peer", "name", c])
    except RuntimeError:
        if check_interface_exists(h, runner=r) and check_interface_exists(c, runner=r):
            return
        raise


def attach_host_if_to_bridge(host_if: str, bridge_name: str, *, runner: CommandRunner | None = None) -> None:
    """
    Attach the host veth leg to a bridge and bring it up.

    ``ip link set dev <host_if> master <bridge>`` then ``ip link set dev <host_if> up``.
    """
    h = _validate_ifname(host_if, label="host_if")
    br = _validate_ifname(bridge_name, label="bridge")
    r = runner or CommandRunner()
    r.run(["ip", "link", "set", "dev", h, "master", br])
    r.run(["ip", "link", "set", "dev", h, "up"])


def move_container_if_to_netns(container_if: str, netns_ref: str, *, runner: CommandRunner | None = None) -> None:
    """
    Move ``container_if`` into the network namespace identified by ``netns_ref``.

    Uses ``ip link set dev <container_if> netns <pid>`` (PID from ``netns_ref``).
    After this call, ``container_if`` is no longer visible in the init/host netns.
    """
    c = _validate_ifname(container_if, label="container_if")
    pid = _pid_from_netns_ref(netns_ref)
    r = runner or CommandRunner()
    r.run(["ip", "link", "set", "dev", c, "netns", pid])


def assign_ip_in_netns(
    netns_ref: str,
    container_if: str,
    workspace_ip: str,
    cidr: str,
    *,
    runner: CommandRunner | None = None,
) -> None:
    """
    Inside the target netns: add ``workspace_ip/<prefix>`` on ``container_if`` and bring the iface up.

    V1 is IPv4 only. ``workspace_ip`` must lie within ``cidr``. Treats ``File exists`` on ``addr add`` as success.
    """
    _pid_from_netns_ref(netns_ref)  # validate early
    ifn = _validate_ifname(container_if, label="container_if")

    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise ValueError(f"invalid cidr: {cidr!r}") from e
    if net.version != 4:
        raise ValueError("V1 attachment ops support IPv4 CIDR only")
    try:
        ip = ipaddress.ip_address(workspace_ip.strip())
    except ValueError as e:
        raise ValueError(f"invalid workspace_ip: {workspace_ip!r}") from e
    if not isinstance(ip, ipaddress.IPv4Address):
        raise ValueError("V1 attachment ops support IPv4 workspace_ip only")
    if ip not in net:
        raise ValueError(f"workspace_ip {workspace_ip!r} not in cidr {cidr!r}")

    addr = f"{ip}/{net.prefixlen}"
    r = runner or CommandRunner()
    prefix = _netns_prefix(netns_ref)
    try:
        r.run([*prefix, "ip", "addr", "add", addr, "dev", ifn])
    except RuntimeError as e:
        if "File exists" in str(e):
            pass
        else:
            raise
    r.run([*prefix, "ip", "link", "set", "dev", ifn, "up"])


def ensure_default_route_in_netns(
    netns_ref: str,
    gateway_ip: str,
    *,
    runner: CommandRunner | None = None,
) -> None:
    """
    Ensure default IPv4 route via ``gateway_ip`` in the target netns.

    Uses ``ip route replace default via <gateway_ip>`` (idempotent).
    """
    _pid_from_netns_ref(netns_ref)
    try:
        gw = ipaddress.ip_address(gateway_ip.strip())
    except ValueError as e:
        raise ValueError(f"invalid gateway_ip: {gateway_ip!r}") from e
    if not isinstance(gw, ipaddress.IPv4Address):
        raise ValueError("V1 attachment ops support IPv4 gateway only")

    r = runner or CommandRunner()
    r.run([*_netns_prefix(netns_ref), "ip", "route", "replace", "default", "via", str(gw)])


def remove_veth_if_exists(host_if: str, *, runner: CommandRunner | None = None) -> None:
    """
    Delete ``host_if`` if present (removes the veth pair).

    ``ip link del dev <host_if>`` — no-op when the interface is already gone.
    """
    h = _validate_ifname(host_if, label="host_if")
    r = runner or CommandRunner()
    if not check_interface_exists(h, runner=r):
        return
    r.run(["ip", "link", "del", "dev", h])
