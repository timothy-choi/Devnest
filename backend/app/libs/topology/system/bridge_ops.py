"""Linux bridge operations for Topology V1 (node-local only).

This module is intentionally small and reversible:
- no veth / container netns work
- no routing, NAT, iptables, or firewall behavior
- idempotent where practical (safe to call repeatedly)

All command execution goes through ``CommandRunner`` so higher-level topology code can mock it.
"""

from __future__ import annotations

import ipaddress

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

