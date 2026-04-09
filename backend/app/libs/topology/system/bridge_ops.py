"""Linux bridge operations for Topology V1 (node-local only).

This module is intentionally small and reversible:
- no veth / container netns work
- no routing, NAT, iptables, or firewall behavior
- idempotent where practical (safe to call repeatedly)

All command execution goes through ``CommandRunner`` so higher-level topology code can mock it.
"""

from __future__ import annotations

import ipaddress
import re

from .command_runner import CommandRunner


def _validate_linux_ifname(name: str) -> str:
    """
    Validate Linux interface name constraints (conservative).

    Linux IFNAMSIZ is typically 16 bytes including NUL → max 15 visible chars.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("bridge_name must be a non-empty string")
    s = name.strip()
    if len(s) > 15:
        raise ValueError(f"bridge_name too long for Linux interface name: {s!r} (len={len(s)})")
    if any(ch.isspace() for ch in s):
        raise ValueError(f"bridge_name must not contain whitespace: {s!r}")
    return s


def check_bridge_exists(bridge_name: str, *, runner: CommandRunner | None = None) -> bool:
    """Return True if the bridge device exists (by name)."""
    br = _validate_linux_ifname(bridge_name)
    r = runner or CommandRunner()
    try:
        r.run(["ip", "link", "show", "dev", br])
        return True
    except RuntimeError:
        return False


def check_bridge_link_up(bridge_name: str, *, runner: CommandRunner | None = None) -> bool:
    """
    Return True if the device exists and ``ip link`` reports administratively UP.

    Parses ``ip link show dev <name>`` for ``state UP`` (best-effort across iproute2 versions).
    """
    br = _validate_linux_ifname(bridge_name)
    r = runner or CommandRunner()
    try:
        out = r.run(["ip", "link", "show", "dev", br])
    except RuntimeError:
        return False
    return bool(re.search(r"\bstate UP\b", out))


def check_bridge_has_ipv4_address(
    bridge_name: str,
    gateway_ip: str,
    cidr: str,
    *,
    runner: CommandRunner | None = None,
) -> bool:
    """
    Return True if ``ip -4 addr`` shows the gateway with the CIDR prefix length on the bridge.

    Read-only health check aligned with ``ensure_bridge_address`` (IPv4 only).
    """
    br = _validate_linux_ifname(bridge_name)
    r = runner or CommandRunner()
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        gw = ipaddress.ip_address(gateway_ip.strip())
    except ValueError:
        return False
    if net.version != 4 or not isinstance(gw, ipaddress.IPv4Address) or gw not in net:
        return False
    want_if = ipaddress.ip_interface(f"{gw}/{net.prefixlen}")
    try:
        out = r.run(["ip", "-o", "-4", "addr", "show", "dev", br])
    except RuntimeError:
        return False
    for line in out.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        i = parts.index("inet")
        if i + 1 >= len(parts):
            continue
        token = parts[i + 1]
        try:
            iface = ipaddress.ip_interface(token)
        except ValueError:
            continue
        if iface.ip == want_if.ip and iface.network.prefixlen == want_if.network.prefixlen:
            return True
    return False


def ensure_bridge_exists(bridge_name: str, *, runner: CommandRunner | None = None) -> None:
    """
    Ensure the bridge exists (idempotent).

    Creates: ``ip link add <bridge_name> type bridge`` when missing.
    """
    br = _validate_linux_ifname(bridge_name)
    r = runner or CommandRunner()
    if check_bridge_exists(br, runner=r):
        return
    # Create bridge; if a concurrent creator raced, a follow-up check will succeed.
    try:
        r.run(["ip", "link", "add", br, "type", "bridge"])
    except RuntimeError:
        if check_bridge_exists(br, runner=r):
            return
        raise


def ensure_bridge_up(bridge_name: str, *, runner: CommandRunner | None = None) -> None:
    """Ensure the bridge exists and is administratively UP (idempotent)."""
    br = _validate_linux_ifname(bridge_name)
    r = runner or CommandRunner()
    ensure_bridge_exists(br, runner=r)
    r.run(["ip", "link", "set", "dev", br, "up"])


def ensure_bridge_address(
    bridge_name: str,
    gateway_ip: str,
    cidr: str,
    *,
    runner: CommandRunner | None = None,
) -> None:
    """
    Ensure the bridge has the gateway IP address for the given CIDR (idempotent best-effort).

    Uses: ``ip addr add <gateway_ip>/<prefixlen> dev <bridge_name>``.
    If the address already exists, the kernel may return an error containing "File exists";
    we treat that as success.
    """
    br = _validate_linux_ifname(bridge_name)
    r = runner or CommandRunner()
    ensure_bridge_exists(br, runner=r)

    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise ValueError(f"invalid cidr: {cidr!r}") from e
    if net.version != 4:
        raise ValueError("V1 bridge ops support IPv4 CIDR only")

    try:
        ip = ipaddress.ip_address(gateway_ip)
    except ValueError as e:
        raise ValueError(f"invalid gateway_ip: {gateway_ip!r}") from e
    if ip not in net:
        raise ValueError(f"gateway_ip {gateway_ip!r} not in cidr {cidr!r}")

    addr = f"{ip}/{net.prefixlen}"
    try:
        r.run(["ip", "addr", "add", addr, "dev", br])
    except RuntimeError as e:
        # Best-effort idempotency without a separate parse step.
        if "File exists" in str(e):
            return
        raise

