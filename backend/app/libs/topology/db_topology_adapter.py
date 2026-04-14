"""
DB-backed ``TopologyAdapter`` (V1): node bridge + control-plane state for IP leases and attachments.

``ensure_node_topology`` applies node-local Linux bridge setup (see ``system.bridge_ops``) unless
disabled via ``apply_linux_bridge=False`` or env ``DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE=1``.

``attach_workspace`` runs veth + netns wiring (see ``system.attachment_ops``) unless disabled via
``apply_linux_attachment=False`` or env ``DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT=1``.

``detach_workspace`` removes the host veth leg when Linux attachment is enabled (same flag/env);
otherwise it updates attachment DB state only. IP leases are not released on detach (V1);
:meth:`release_workspace_ip_lease` releases the DB lease explicitly (failed bring-up / reconcile).

``check_topology`` / ``check_attachment`` append ``linux: …`` issues when the corresponding
``apply_linux_*`` flag is enabled (see env ``DEVNEST_TOPOLOGY_SKIP_LINUX_*``); otherwise they
evaluate persisted state only. Issue prefixes: ``db:`` (rows/fields), ``runtime:`` (CIDR/gateway
consistency), ``linux:`` (host ``ip`` checks). Unexpected ``RuntimeError`` from ``ip`` is surfaced
as ``linux:`` issues instead of aborting the check.

``delete_topology`` removes the ``TopologyRuntime`` row (and node-local attachment rows) when no
non-``DETACHED`` attachments exist, commits, then optionally deletes the Linux bridge when bridge sync
is enabled (so DB state is not lost if bridge removal fails).
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT

from .errors import (
    TopologyDeleteError,
    TopologyRuntimeCreateError,
    TopologyRuntimeNotFoundError,
    WorkspaceAttachmentError,
    WorkspaceDetachError,
    WorkspaceIPAllocationError,
)
from .interfaces import TopologyAdapter
from .models import (
    IpAllocation,
    Topology,
    TopologyAttachment,
    TopologyRuntime,
)
from .models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus
from .results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    CheckAttachmentResult,
    CheckTopologyResult,
    DetachWorkspaceResult,
    EnsureNodeTopologyResult,
)

if TYPE_CHECKING:
    from .system.command_runner import CommandRunner

_V1_MODE = "node_bridge"

# V1 default CIDR allocator: each node gets a stable /20 out of the parent pool.
_V1_PARENT_POOL_CIDR = "10.128.0.0/9"
_V1_CHILD_PREFIXLEN = 20

# Reserve the first N usable host IPs in each runtime CIDR for infra (in addition to the gateway).
# With the default gateway as the first usable host, this typically makes workspace allocation start at .11.
_V1_INFRA_RESERVED_USABLE_HOSTS = 10

# Retries after IntegrityError when persisting a runtime CIDR (concurrent ensure calls).
_ENSURE_NODE_CIDR_MAX_ATTEMPTS = 16


def _bridge_name_for(topology_id: int, node_id: str) -> str:
    """Linux interface name max 15 chars; deterministic from topology + node."""
    h = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:4]
    raw = f"dn{topology_id}{h}"
    return raw[:15]


def _spec_mode(spec: dict) -> str:
    m = spec.get("mode")
    if m is None:
        return _V1_MODE
    if isinstance(m, str):
        return m.strip().lower().replace("-", "_")
    return str(m)


def _node_bridge_plan(topology: Topology, node_id: str) -> tuple[str | None, str | None, str]:
    """
    Return (spec_cidr, spec_gateway_ip, bridge_name) for V1 node_bridge.

    Optional ``spec_json`` keys: ``bridge_name``, ``cidr``, ``gateway_ip``, ``mode``.
    """
    spec = topology.spec_json if isinstance(topology.spec_json, dict) else {}
    mode = _spec_mode(spec)
    if mode != _V1_MODE:
        raise TopologyRuntimeCreateError(
            f"topology V1 supports mode {_V1_MODE!r} only; got {mode!r}",
        )
    cidr_raw = spec.get("cidr")
    spec_cidr = cidr_raw.strip() if isinstance(cidr_raw, str) and cidr_raw.strip() else None
    gw_raw = spec.get("gateway_ip")
    spec_gateway_ip = gw_raw.strip() if isinstance(gw_raw, str) and gw_raw.strip() else None
    bridge_raw = spec.get("bridge_name")
    if isinstance(bridge_raw, str) and bridge_raw.strip():
        # Must fit Linux IFNAMSIZ (15 chars); matches ``bridge_ops`` validation.
        bridge_name = bridge_raw.strip()[:15]
    else:
        bridge_name = _bridge_name_for(topology.topology_id or 0, node_id)
    return spec_cidr, spec_gateway_ip, bridge_name


def _parse_ipv4_network(*, cidr: str, ctx: str) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise TopologyRuntimeCreateError(f"invalid cidr {ctx}: {cidr!r}") from e
    if not isinstance(net, ipaddress.IPv4Network):
        raise TopologyRuntimeCreateError("V1 runtime CIDR must be IPv4")
    return net


def _first_usable_host(net: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    for h in net.hosts():
        if isinstance(h, ipaddress.IPv4Address):
            return h
    raise TopologyRuntimeCreateError(f"no usable hosts in CIDR {str(net)!r}")


def _list_used_runtime_networks(
    session: Session,
    *,
    topology_id: int,
    exclude_node_id: str | None = None,
) -> list[ipaddress.IPv4Network]:
    stmt = select(TopologyRuntime.cidr, TopologyRuntime.node_id).where(TopologyRuntime.topology_id == topology_id)
    out: list[ipaddress.IPv4Network] = []
    for cidr, node in session.exec(stmt).all():
        if exclude_node_id is not None and node == exclude_node_id:
            continue
        if not cidr or not str(cidr).strip():
            continue
        try:
            net_any = ipaddress.ip_network(str(cidr).strip(), strict=False)
        except ValueError:
            # Be conservative: if DB contains a malformed CIDR, ignore it for allocation purposes
            # (it shouldn't happen, but we don't want ensure to crash unrelated nodes).
            continue
        if isinstance(net_any, ipaddress.IPv4Network):
            out.append(net_any)
    return out


def _net_overlaps_any(net: ipaddress.IPv4Network, used: list[ipaddress.IPv4Network]) -> bool:
    return any(net.overlaps(u) for u in used)


def _iter_pool_child_subnets() -> list[ipaddress.IPv4Network]:
    parent = _parse_ipv4_network(cidr=_V1_PARENT_POOL_CIDR, ctx="for V1 parent pool")
    return list(parent.subnets(new_prefix=_V1_CHILD_PREFIXLEN))


def _allocate_child_subnet(
    session: Session,
    *,
    topology_id: int,
    node_id: str,
    retry_index: int = 0,
) -> ipaddress.IPv4Network:
    used = _list_used_runtime_networks(session, topology_id=topology_id, exclude_node_id=node_id)
    children = _iter_pool_child_subnets()
    n = len(children)
    if n == 0:
        raise TopologyRuntimeCreateError(f"parent pool {_V1_PARENT_POOL_CIDR!r} produced no /{_V1_CHILD_PREFIXLEN} children")
    # Rotate scan only on retry (attempt 0 keeps first-free child for stable defaults).
    start = 0
    if retry_index > 0:
        start = (retry_index * 7919 + topology_id * 17 + sum(map(ord, node_id))) % n
    for k in range(n):
        child = children[(start + k) % n]
        if not _net_overlaps_any(child, used):
            return child
    raise TopologyRuntimeCreateError(
        f"no free /{_V1_CHILD_PREFIXLEN} subnets remaining in parent pool {_V1_PARENT_POOL_CIDR!r}",
    )


def _choose_runtime_cidr_and_gateway(
    session: Session,
    *,
    topology_id: int,
    node_id: str,
    existing: TopologyRuntime | None,
    spec_cidr: str | None,
    spec_gateway_ip: str | None,
    pool_retry_index: int = 0,
) -> tuple[str, str]:
    # 1) Existing runtime with CIDR: reuse (and fill missing gateway if needed).
    if existing is not None and existing.cidr and str(existing.cidr).strip():
        net = _parse_ipv4_network(cidr=str(existing.cidr).strip(), ctx="on existing runtime")
        if existing.gateway_ip and str(existing.gateway_ip).strip():
            gw = str(existing.gateway_ip).strip()
            g = ipaddress.ip_address(gw)
            if g not in net:
                raise TopologyRuntimeCreateError(f"existing gateway_ip {gw!r} not in runtime CIDR {str(net)!r}")
            return str(net), gw
        return str(net), str(_first_usable_host(net))

    # 2) Spec CIDR: validate and require non-overlap with other nodes in this topology.
    if spec_cidr is not None:
        net = _parse_ipv4_network(cidr=spec_cidr, ctx="in topology spec")
        used = _list_used_runtime_networks(session, topology_id=topology_id, exclude_node_id=node_id)
        if _net_overlaps_any(net, used):
            raise TopologyRuntimeCreateError(
                f"spec cidr {str(net)!r} conflicts with an existing topology runtime CIDR",
            )
        if spec_gateway_ip is not None:
            try:
                g = ipaddress.ip_address(spec_gateway_ip)
            except ValueError as e:
                raise TopologyRuntimeCreateError(f"invalid gateway_ip in spec: {spec_gateway_ip!r}") from e
            if g not in net:
                raise TopologyRuntimeCreateError(f"gateway_ip {g} not in network {net}")
            return str(net), str(g)
        return str(net), str(_first_usable_host(net))

    # 3) Auto allocate from parent pool.
    net = _allocate_child_subnet(
        session,
        topology_id=topology_id,
        node_id=node_id,
        retry_index=pool_retry_index,
    )
    if spec_gateway_ip is not None:
        try:
            g = ipaddress.ip_address(spec_gateway_ip)
        except ValueError as e:
            raise TopologyRuntimeCreateError(f"invalid gateway_ip in spec: {spec_gateway_ip!r}") from e
        if g not in net:
            raise TopologyRuntimeCreateError(f"gateway_ip {g} not in allocated subnet {net}")
        return str(net), str(g)
    return str(net), str(_first_usable_host(net))


def _runtime_row_complete(row: TopologyRuntime) -> bool:
    return bool(
        row.bridge_name
        and str(row.bridge_name).strip()
        and row.cidr
        and str(row.cidr).strip()
        and row.gateway_ip
        and str(row.gateway_ip).strip()
    )


def _runtime_to_ensure_result(row: TopologyRuntime) -> EnsureNodeTopologyResult:
    assert row.topology_runtime_id is not None
    return EnsureNodeTopologyResult(
        topology_runtime_id=row.topology_runtime_id,
        bridge_name=row.bridge_name,
        cidr=row.cidr,
        gateway_ip=row.gateway_ip,
        status=row.status,
    )


def _gateway_as_address(cidr: str, gateway_ip: str) -> ipaddress.IPv4Address:
    try:
        g = ipaddress.ip_address(gateway_ip)
    except ValueError as e:
        raise WorkspaceIPAllocationError(f"invalid gateway_ip on runtime: {gateway_ip!r}") from e
    net = ipaddress.ip_network(cidr, strict=False)
    if g not in net:
        raise WorkspaceIPAllocationError(f"gateway {g} not in runtime CIDR {net}")
    if isinstance(g, ipaddress.IPv6Address):
        raise WorkspaceIPAllocationError("V1 allocation is IPv4 only")
    return g


# Retries after IntegrityError (concurrent allocate on same workspace or same chosen IP).
_ALLOCATE_IP_MAX_ATTEMPTS = 24


def _iter_candidate_workspace_hosts(cidr: str, gateway_ip: str) -> list[ipaddress.IPv4Address]:
    """Workspace host candidates: skip gateway and reserve the first N usable host IPs for infra."""
    net = ipaddress.ip_network(cidr, strict=False)
    gw = _gateway_as_address(cidr, gateway_ip)
    out: list[ipaddress.IPv4Address] = []
    usable_idx = 0
    for h in net.hosts():
        if not isinstance(h, ipaddress.IPv4Address):
            continue
        usable_idx += 1
        if h == gw:
            continue
        # Reserve early usable host IPs (1-based) regardless of which is chosen as gateway.
        # If the subnet is too small to accommodate the reservation, fall back to "no reservation"
        # rather than making the pool unusable.
        if usable_idx <= _V1_INFRA_RESERVED_USABLE_HOSTS:
            continue
        out.append(h)
    if not out and usable_idx > 0 and usable_idx <= _V1_INFRA_RESERVED_USABLE_HOSTS:
        # Retry without infra reservation for tiny CIDRs (/30 etc.), still skipping gateway.
        for h in net.hosts():
            if not isinstance(h, ipaddress.IPv4Address):
                continue
            if h == gw:
                continue
            out.append(h)
    return out


def _pick_first_free_host(
    candidates: list[ipaddress.IPv4Address],
    used_ips: set[ipaddress.IPv4Address],
    *,
    scan_offset: int,
) -> ipaddress.IPv4Address | None:
    """Return first unused host in ``candidates``, scanning from ``scan_offset`` (wraps). ``scan_offset`` 0 preserves V1 order."""
    n = len(candidates)
    if n == 0:
        return None
    offset = scan_offset % n
    for i in range(n):
        h = candidates[(offset + i) % n]
        if h not in used_ips:
            return h
    return None


def _runtime_consistency_issues(*, cidr: str | None, gateway_ip: str | None) -> list[str]:
    issues: list[str] = []
    if not (cidr and str(cidr).strip()):
        return issues
    try:
        net_any = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError as e:
        issues.append(f"runtime: cidr is invalid ({e})")
        return issues
    if not isinstance(net_any, ipaddress.IPv4Network):
        issues.append("runtime: cidr is not IPv4")
        return issues

    if gateway_ip and str(gateway_ip).strip():
        try:
            g_any = ipaddress.ip_address(str(gateway_ip).strip())
        except ValueError as e:
            issues.append(f"runtime: gateway_ip is invalid ({e})")
            return issues
        if not isinstance(g_any, ipaddress.IPv4Address):
            issues.append("runtime: gateway_ip is not IPv4")
            return issues
        if g_any not in net_any:
            issues.append("runtime: gateway_ip is not within cidr")
    return issues


def _apply_linux_bridge_env_default() -> bool:
    """When true, ``ensure_node_topology`` runs ``ip`` bridge setup on the host."""
    return os.environ.get("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "").lower() not in (
        "1",
        "true",
        "yes",
    )


def _apply_linux_attachment_env_default() -> bool:
    """When true, ``attach_workspace`` runs veth/netns ``ip`` + ``nsenter`` steps."""
    return os.environ.get("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", "").lower() not in (
        "1",
        "true",
        "yes",
    )


def _veth_pair_names(topology_id: int, node_id: str, workspace_id: int) -> tuple[str, str]:
    """Deterministic Linux interface names (≤15 chars) for host leg and container peer."""
    h = hashlib.sha256(f"{topology_id}:{node_id}:{workspace_id}".encode("utf-8")).hexdigest()[:6]
    return f"vh{h}", f"vc{h}"


class DbTopologyAdapter(TopologyAdapter):
    """
    Persist ``TopologyRuntime``, ``IpAllocation``, and ``TopologyAttachment`` for V1 node_bridge.

    Pass a request-scoped or unit-of-work ``Session``; each public method commits on success.
    """

    def __init__(
        self,
        session: Session,
        *,
        command_runner: CommandRunner | None = None,
        apply_linux_bridge: bool | None = None,
        apply_linux_attachment: bool | None = None,
    ) -> None:
        self._session = session
        self._command_runner = command_runner
        self._apply_linux_bridge = (
            apply_linux_bridge if apply_linux_bridge is not None else _apply_linux_bridge_env_default()
        )
        self._apply_linux_attachment = (
            apply_linux_attachment
            if apply_linux_attachment is not None
            else _apply_linux_attachment_env_default()
        )

    def _sync_linux_node_bridge(self, row: TopologyRuntime) -> None:
        """
        Ensure bridge exists, is up, and has gateway/cidr address; update row status and error fields.

        On failure: ``DEGRADED`` + ``last_error_*`` (still commits). On success: ``READY`` and clears errors.
        """
        from .system.bridge_ops import ensure_bridge_address, ensure_bridge_exists, ensure_bridge_up
        from .system.command_runner import CommandRunner

        now = datetime.now(timezone.utc)
        runner = self._command_runner or CommandRunner()

        if not self._apply_linux_bridge:
            row.status = TopologyRuntimeStatus.READY
            row.last_error_code = None
            row.last_error_message = None
            row.last_checked_at = now
            row.updated_at = now
            self._session.add(row)
            self._session.commit()
            self._session.refresh(row)
            return

        br, cidr, gw = row.bridge_name, row.cidr, row.gateway_ip
        if not (br and str(br).strip() and cidr and str(cidr).strip() and gw and str(gw).strip()):
            row.status = TopologyRuntimeStatus.DEGRADED
            row.last_error_code = "INCOMPLETE_RUNTIME"
            row.last_error_message = "topology runtime missing bridge_name, cidr, or gateway_ip"
            row.last_checked_at = now
            row.updated_at = now
            self._session.add(row)
            self._session.commit()
            self._session.refresh(row)
            return

        try:
            ensure_bridge_exists(br, runner=runner)
            ensure_bridge_up(br, runner=runner)
            ensure_bridge_address(br, str(gw).strip(), str(cidr).strip(), runner=runner)
        except ValueError as e:
            row.status = TopologyRuntimeStatus.DEGRADED
            row.last_error_code = "BRIDGE_CONFIG"
            row.last_error_message = str(e)[:2048]
        except RuntimeError as e:
            row.status = TopologyRuntimeStatus.DEGRADED
            row.last_error_code = "BRIDGE_OS"
            row.last_error_message = str(e)[:2048]
        else:
            row.status = TopologyRuntimeStatus.READY
            row.last_error_code = None
            row.last_error_message = None

        row.last_checked_at = now
        row.updated_at = now
        self._session.add(row)
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise TopologyRuntimeCreateError(f"failed to persist topology runtime after bridge sync: {e}") from e
        self._session.refresh(row)

    def _run_linux_attach(
        self,
        *,
        host_if: str,
        container_if: str,
        bridge_name: str,
        cidr: str,
        gateway_ip: str,
        workspace_ip: str,
        netns_ref: str,
    ) -> None:
        from .system import attachment_ops as ao
        from .system.command_runner import CommandRunner

        r = self._command_runner or CommandRunner()
        try:
            ao.create_veth_pair(host_if, container_if, runner=r)
            ao.attach_host_if_to_bridge(host_if, bridge_name, runner=r)
            ao.move_container_if_to_netns(container_if, netns_ref, runner=r)
            ao.assign_ip_in_netns(netns_ref, container_if, workspace_ip, cidr, runner=r)
            ao.ensure_default_route_in_netns(netns_ref, gateway_ip, runner=r)
        except (RuntimeError, ValueError):
            try:
                ao.remove_veth_if_exists(host_if, runner=r)
            except RuntimeError:
                pass
            raise

    def ensure_node_topology(self, *, topology_id: int, node_id: str) -> EnsureNodeTopologyResult:
        if not node_id or not node_id.strip():
            raise TopologyRuntimeCreateError("node_id is required")
        node_id = node_id.strip()[:128]
        topo = self._session.get(Topology, topology_id)
        if topo is None:
            raise TopologyRuntimeCreateError(f"topology id {topology_id} not found")
        stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        existing = self._session.exec(stmt).first()

        spec_cidr, spec_gateway_ip, bridge_name = _node_bridge_plan(topo, node_id)
        now = datetime.now(timezone.utc)

        # Idempotency fast-path: if the runtime row is already complete, keep it stable and avoid DB writes.
        if existing is not None and _runtime_row_complete(existing):
            self._sync_linux_node_bridge(existing)
            return _runtime_to_ensure_result(existing)

        # (Re)assign CIDR/gateway if missing; otherwise keep stable.
        for attempt in range(_ENSURE_NODE_CIDR_MAX_ATTEMPTS):
            existing = self._session.exec(stmt).first()
            if existing is not None and _runtime_row_complete(existing):
                break
            try:
                cidr, gateway_ip = _choose_runtime_cidr_and_gateway(
                    self._session,
                    topology_id=topology_id,
                    node_id=node_id,
                    existing=existing,
                    spec_cidr=spec_cidr,
                    spec_gateway_ip=spec_gateway_ip,
                    pool_retry_index=attempt,
                )
                if existing is None:
                    row = TopologyRuntime(
                        topology_id=topology_id,
                        node_id=node_id,
                        status=TopologyRuntimeStatus.READY,
                        bridge_name=bridge_name,
                        cidr=cidr,
                        gateway_ip=gateway_ip,
                        nat_enabled=None,
                        iptables_profile=None,
                        managed_by_agent=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self._session.add(row)
                else:
                    changed = False
                    if not (existing.bridge_name and str(existing.bridge_name).strip()):
                        existing.bridge_name = bridge_name
                        changed = True
                    if not (existing.cidr and str(existing.cidr).strip()):
                        existing.cidr = cidr
                        changed = True
                    if not (existing.gateway_ip and str(existing.gateway_ip).strip()):
                        existing.gateway_ip = gateway_ip
                        changed = True
                    if changed:
                        existing.updated_at = now
                        self._session.add(existing)
                if self._session.new or self._session.dirty:
                    self._session.commit()
                break
            except IntegrityError as exc:
                # Concurrency safety:
                # - uq_topology_runtime_topology_node: another worker created the row; reload and continue.
                # - uq_topology_runtime_topology_cidr: another worker claimed the same CIDR; retry auto-alloc.
                self._session.rollback()
                self._session.expire_all()
                if attempt + 1 >= _ENSURE_NODE_CIDR_MAX_ATTEMPTS:
                    raise TopologyRuntimeCreateError(
                        "failed to allocate a unique runtime CIDR after repeated DB conflicts",
                    ) from exc
                continue
            except TopologyRuntimeCreateError:
                self._session.rollback()
                raise
            except Exception as e:
                self._session.rollback()
                raise TopologyRuntimeCreateError(f"failed to persist topology runtime: {e}") from e

        row = self._session.exec(stmt).first()
        if row is None:
            raise TopologyRuntimeCreateError("failed to ensure topology runtime row")
        self._session.refresh(row)
        self._sync_linux_node_bridge(row)
        return _runtime_to_ensure_result(row)

    def allocate_workspace_ip(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> AllocateWorkspaceIPResult:
        node_id = node_id.strip()[:128]
        rt_stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        runtime = self._session.exec(rt_stmt).first()
        if runtime is None:
            raise TopologyRuntimeNotFoundError(
                f"no topology runtime for topology_id={topology_id} node_id={node_id!r}",
            )
        if runtime.status != TopologyRuntimeStatus.READY:
            raise WorkspaceIPAllocationError(
                f"topology runtime is not READY (status={runtime.status.value}); "
                "cannot allocate workspace IP until bridge/sync is healthy",
            )
        if not runtime.cidr or not runtime.gateway_ip:
            raise WorkspaceIPAllocationError("topology runtime missing cidr or gateway_ip")

        for attempt in range(_ALLOCATE_IP_MAX_ATTEMPTS):
            try:
                return self._allocate_workspace_ip_attempt(
                    topology_id=topology_id,
                    node_id=node_id,
                    workspace_id=workspace_id,
                    retry_index=attempt,
                )
            except IntegrityError as exc:
                self._session.rollback()
                self._session.expire_all()
                runtime = self._session.exec(rt_stmt).first()
                if runtime is None:
                    raise TopologyRuntimeNotFoundError(
                        f"no topology runtime for topology_id={topology_id} node_id={node_id!r}",
                    ) from exc
                if runtime.status != TopologyRuntimeStatus.READY:
                    raise WorkspaceIPAllocationError(
                        f"topology runtime is not READY (status={runtime.status.value}); "
                        "cannot allocate workspace IP until bridge/sync is healthy",
                    ) from exc
                if not runtime.cidr or not runtime.gateway_ip:
                    raise WorkspaceIPAllocationError(
                        "topology runtime missing cidr or gateway_ip",
                    ) from exc
                if attempt + 1 >= _ALLOCATE_IP_MAX_ATTEMPTS:
                    raise WorkspaceIPAllocationError(
                        "IP allocation hit repeated database conflicts (likely concurrent requests); "
                        f"giving up after {_ALLOCATE_IP_MAX_ATTEMPTS} attempts",
                    ) from exc
                continue

        raise WorkspaceIPAllocationError(
            "internal error: IP allocation loop exited without result or handled exception",
        )

    def _allocate_workspace_ip_attempt(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
        retry_index: int = 0,
    ) -> AllocateWorkspaceIPResult:
        # Re-load runtime each attempt so CIDR/gateway reflect DB after rollback/retry.
        rt_stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        fresh = self._session.exec(rt_stmt).first()
        if fresh is None:
            raise TopologyRuntimeNotFoundError(
                f"no topology runtime for topology_id={topology_id} node_id={node_id!r}",
            )
        runtime = fresh
        if runtime.status != TopologyRuntimeStatus.READY:
            raise WorkspaceIPAllocationError(
                f"topology runtime is not READY (status={runtime.status.value}); "
                "cannot allocate workspace IP until bridge/sync is healthy",
            )
        if not runtime.cidr or not runtime.gateway_ip:
            raise WorkspaceIPAllocationError("topology runtime missing cidr or gateway_ip")

        alloc_stmt = select(IpAllocation).where(
            IpAllocation.topology_id == topology_id,
            IpAllocation.node_id == node_id,
            IpAllocation.workspace_id == workspace_id,
        )
        row = self._session.exec(alloc_stmt).first()
        if row is not None and row.released_at is None:
            return AllocateWorkspaceIPResult(workspace_ip=row.ip, leased_existing=True)

        candidates = _iter_candidate_workspace_hosts(runtime.cidr, runtime.gateway_ip)
        used_stmt = select(IpAllocation.ip).where(
            IpAllocation.topology_id == topology_id,
            IpAllocation.node_id == node_id,
            IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
        )
        used_ips = {ipaddress.ip_address(s) for s in self._session.exec(used_stmt).all()}
        if not candidates:
            raise WorkspaceIPAllocationError("no free IPv4 addresses in topology CIDR")
        scan_offset = 0
        if retry_index > 0:
            # Spread concurrent retries: otherwise every loser re-picks the same "first free" host.
            scan_offset = (retry_index * 10_007 + workspace_id * 31 + sum(map(ord, node_id))) % len(
                candidates,
            )
        maybe_chosen = _pick_first_free_host(candidates, used_ips, scan_offset=scan_offset)
        if maybe_chosen is None:
            raise WorkspaceIPAllocationError("no free IPv4 addresses in topology CIDR")
        chosen = maybe_chosen

        now = datetime.now(timezone.utc)
        ip_str = str(chosen)
        if row is None:
            row = IpAllocation(
                node_id=node_id,
                topology_id=topology_id,
                workspace_id=workspace_id,
                ip=ip_str,
                leased_at=now,
                released_at=None,
            )
            self._session.add(row)
        else:
            row.ip = ip_str
            row.leased_at = now
            row.released_at = None
            self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            raise
        except Exception as e:
            self._session.rollback()
            raise WorkspaceIPAllocationError(f"failed to persist IP allocation: {e}") from e
        self._session.refresh(row)
        return AllocateWorkspaceIPResult(workspace_ip=ip_str, leased_existing=False)

    def attach_workspace(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
        container_id: str,
        netns_ref: str,
        workspace_ip: str,
    ) -> AttachWorkspaceResult:
        if not container_id or not container_id.strip():
            raise WorkspaceAttachmentError("container_id is required")
        container_id = container_id.strip()[:128]
        node_id = node_id.strip()[:128]
        workspace_ip = workspace_ip.strip()

        self.ensure_node_topology(topology_id=topology_id, node_id=node_id)
        # ``ensure_node_topology`` commits inside ``_sync_linux_node_bridge``; reload ORM state.
        self._session.expire_all()

        rt_stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        runtime = self._session.exec(rt_stmt).first()
        if runtime is None:
            raise TopologyRuntimeNotFoundError(
                f"no topology runtime for topology_id={topology_id} node_id={node_id!r}",
            )
        if runtime.status != TopologyRuntimeStatus.READY:
            raise WorkspaceAttachmentError(
                f"topology runtime is not READY (status={runtime.status.value}); cannot attach workspace",
            )
        if not (runtime.bridge_name and str(runtime.bridge_name).strip()):
            raise WorkspaceAttachmentError("topology runtime missing bridge_name")
        if not (runtime.cidr and str(runtime.cidr).strip() and runtime.gateway_ip and str(runtime.gateway_ip).strip()):
            raise WorkspaceAttachmentError("topology runtime missing cidr or gateway_ip")

        alloc_stmt = select(IpAllocation).where(
            IpAllocation.topology_id == topology_id,
            IpAllocation.node_id == node_id,
            IpAllocation.workspace_id == workspace_id,
            IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
        )
        alloc = self._session.exec(alloc_stmt).first()
        if alloc is None or alloc.ip != workspace_ip:
            raise WorkspaceAttachmentError(
                "workspace_ip must match an active allocation; call allocate_workspace_ip first",
            )
        try:
            ws_addr = ipaddress.ip_address(workspace_ip)
            rt_net = ipaddress.ip_network(str(runtime.cidr).strip(), strict=False)
            if not isinstance(ws_addr, ipaddress.IPv4Address) or not isinstance(
                rt_net,
                ipaddress.IPv4Network,
            ):
                raise WorkspaceAttachmentError("V1 attach requires IPv4 workspace_ip and runtime CIDR")
            if ws_addr not in rt_net:
                raise WorkspaceAttachmentError(
                    f"workspace_ip {workspace_ip!r} is not within runtime CIDR {runtime.cidr!r}",
                )
        except ValueError as e:
            raise WorkspaceAttachmentError(f"invalid workspace_ip or runtime cidr: {e}") from e

        att_stmt = select(TopologyAttachment).where(
            TopologyAttachment.topology_id == topology_id,
            TopologyAttachment.node_id == node_id,
            TopologyAttachment.workspace_id == workspace_id,
        )
        att = self._session.exec(att_stmt).first()
        att_was_new = att is None

        internal_endpoint = f"{workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        if (
            att is not None
            and att.status == TopologyAttachmentStatus.ATTACHED
            and (att.container_id or "") == container_id
            and (att.workspace_ip or "") == workspace_ip
        ):
            assert att.attachment_id is not None
            return AttachWorkspaceResult(
                attachment_id=att.attachment_id,
                workspace_ip=workspace_ip,
                bridge_name=runtime.bridge_name,
                gateway_ip=runtime.gateway_ip,
                internal_endpoint=internal_endpoint,
            )

        from .system.attachment_ops import validate_netns_ref
        from .system.command_runner import CommandRunner

        netns_clean: str | None = None
        if self._apply_linux_attachment:
            try:
                netns_clean = validate_netns_ref(netns_ref)
            except ValueError as e:
                raise WorkspaceAttachmentError(str(e)) from e
        elif not (isinstance(netns_ref, str) and netns_ref.strip()):
            raise WorkspaceAttachmentError("netns_ref is required")

        host_if, ctr_if = _veth_pair_names(topology_id, node_id, workspace_id)

        if self._apply_linux_attachment and att is not None and att.interface_host:
            try:
                from .system.attachment_ops import remove_veth_if_exists

                remove_veth_if_exists(
                    str(att.interface_host).strip(),
                    runner=self._command_runner or CommandRunner(),
                )
            except RuntimeError:
                pass

        now = datetime.now(timezone.utc)
        if att is None:
            att = TopologyAttachment(
                topology_id=topology_id,
                node_id=node_id,
                workspace_id=workspace_id,
                container_id=container_id,
                status=TopologyAttachmentStatus.ATTACHING,
                workspace_ip=workspace_ip,
                bridge_name=runtime.bridge_name,
                gateway_ip=runtime.gateway_ip,
                interface_host=host_if,
                interface_container=ctr_if,
                created_at=now,
                updated_at=now,
            )
            self._session.add(att)
        else:
            att.container_id = container_id
            att.workspace_ip = workspace_ip
            att.bridge_name = runtime.bridge_name
            att.gateway_ip = runtime.gateway_ip
            att.interface_host = host_if
            att.interface_container = ctr_if
            att.status = TopologyAttachmentStatus.ATTACHING
            att.updated_at = now
            self._session.add(att)
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise WorkspaceAttachmentError(f"failed to persist topology attachment: {e}") from e
        self._session.refresh(att)
        assert att.attachment_id is not None

        if not self._apply_linux_attachment:
            try:
                att.status = TopologyAttachmentStatus.ATTACHED
                att.updated_at = datetime.now(timezone.utc)
                self._session.add(att)
                self._session.commit()
            except Exception as e:
                # If we can't persist ATTACHED, leave the system retryable:
                # - mark the attachment FAILED (no dangling ATTACHING row)
                # - release the IP lease only when this call created the attachment row
                self._session.rollback()
                self._attach_failure_best_effort(
                    topology_id=topology_id,
                    node_id=node_id,
                    workspace_id=workspace_id,
                    attachment_id=att.attachment_id,
                    interface_host=host_if,
                    workspace_ip=workspace_ip,
                    release_ip=att_was_new,
                    error=f"persist attach failed: {e}",
                )
                raise WorkspaceAttachmentError(f"failed to persist topology attachment: {e}") from e
            self._session.refresh(att)
            return AttachWorkspaceResult(
                attachment_id=att.attachment_id,
                workspace_ip=workspace_ip,
                bridge_name=runtime.bridge_name,
                gateway_ip=runtime.gateway_ip,
                internal_endpoint=internal_endpoint,
            )

        assert netns_clean is not None
        try:
            self._run_linux_attach(
                host_if=host_if,
                container_if=ctr_if,
                bridge_name=str(runtime.bridge_name).strip(),
                cidr=str(runtime.cidr).strip(),
                gateway_ip=str(runtime.gateway_ip).strip(),
                workspace_ip=workspace_ip,
                netns_ref=netns_clean,
            )
        except (RuntimeError, ValueError) as e:
            self._session.rollback()
            self._attach_failure_best_effort(
                topology_id=topology_id,
                node_id=node_id,
                workspace_id=workspace_id,
                attachment_id=att.attachment_id,
                interface_host=host_if,
                workspace_ip=workspace_ip,
                release_ip=att_was_new,
                error=f"linux attach failed: {e}",
            )
            raise WorkspaceAttachmentError(f"linux attach failed: {e}") from e

        try:
            att.status = TopologyAttachmentStatus.ATTACHED
            att.updated_at = datetime.now(timezone.utc)
            self._session.add(att)
            self._session.commit()
        except Exception as e:
            # Linux attach succeeded but DB update failed. Best-effort rollback so the system is retryable:
            # remove host veth, mark FAILED, and release IP if this call created the attachment row.
            self._session.rollback()
            self._attach_failure_best_effort(
                topology_id=topology_id,
                node_id=node_id,
                workspace_id=workspace_id,
                attachment_id=att.attachment_id,
                interface_host=host_if,
                workspace_ip=workspace_ip,
                release_ip=att_was_new,
                error=f"persist attach failed after linux wiring: {e}",
            )
            raise WorkspaceAttachmentError(f"failed to persist topology attachment: {e}") from e
        self._session.refresh(att)
        return AttachWorkspaceResult(
            attachment_id=att.attachment_id,
            workspace_ip=workspace_ip,
            bridge_name=runtime.bridge_name,
            gateway_ip=runtime.gateway_ip,
            internal_endpoint=internal_endpoint,
        )

    def _attach_failure_best_effort(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
        attachment_id: int,
        interface_host: str | None,
        workspace_ip: str,
        release_ip: bool,
        error: str,
    ) -> None:
        """Best-effort rollback for attach failures after ATTACHING is persisted."""
        # 1) Best-effort host veth cleanup (idempotent).
        if self._apply_linux_attachment:
            try:
                self._linux_detach_host_veth(
                    topology_id=topology_id,
                    node_id=node_id,
                    workspace_id=workspace_id,
                    interface_host=interface_host,
                )
            except Exception:
                pass

        # 2) Mark attachment FAILED so we never leave ATTACHING stuck.
        try:
            att = self._session.get(TopologyAttachment, attachment_id)
            if att is not None:
                att.status = TopologyAttachmentStatus.FAILED
                att.updated_at = datetime.now(timezone.utc)
                self._session.add(att)
                self._session.commit()
        except Exception:
            self._session.rollback()

        # 3) Release IP lease if this call created the attachment and never completed.
        if not release_ip:
            return
        try:
            alloc_stmt = select(IpAllocation).where(
                IpAllocation.topology_id == topology_id,
                IpAllocation.node_id == node_id,
                IpAllocation.workspace_id == workspace_id,
                IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
            )
            alloc = self._session.exec(alloc_stmt).first()
            if alloc is not None and (alloc.ip or "") == workspace_ip:
                alloc.released_at = datetime.now(timezone.utc)
                self._session.add(alloc)
                self._session.commit()
        except Exception:
            self._session.rollback()

    def _linux_detach_host_veth(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
        interface_host: str | None,
    ) -> None:
        """Best-effort removal of the host-side veth for this attachment (idempotent if already gone)."""
        from .system.attachment_ops import remove_veth_if_exists
        from .system.command_runner import CommandRunner

        if interface_host and str(interface_host).strip():
            host_nm = str(interface_host).strip()
        else:
            host_nm, _ = _veth_pair_names(topology_id, node_id, workspace_id)
        r = self._command_runner or CommandRunner()
        try:
            remove_veth_if_exists(host_nm, runner=r)
        except RuntimeError as e:
            raise WorkspaceDetachError(f"linux detach failed: {e}") from e

    def detach_workspace(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> DetachWorkspaceResult:
        node_id = node_id.strip()[:128]
        stmt = select(TopologyAttachment).where(
            TopologyAttachment.topology_id == topology_id,
            TopologyAttachment.node_id == node_id,
            TopologyAttachment.workspace_id == workspace_id,
        )
        att = self._session.exec(stmt).first()
        if att is None:
            return DetachWorkspaceResult(
                detached=False,
                status=TopologyAttachmentStatus.DETACHED,
                workspace_id=workspace_id,
                workspace_ip=None,
                released_ip=False,
            )
        prev_ip = att.workspace_ip
        if att.status == TopologyAttachmentStatus.DETACHED:
            return DetachWorkspaceResult(
                detached=False,
                status=TopologyAttachmentStatus.DETACHED,
                workspace_id=workspace_id,
                workspace_ip=prev_ip,
                released_ip=False,
            )
        if self._apply_linux_attachment:
            self._linux_detach_host_veth(
                topology_id=topology_id,
                node_id=node_id,
                workspace_id=workspace_id,
                interface_host=att.interface_host,
            )

        now = datetime.now(timezone.utc)
        att.status = TopologyAttachmentStatus.DETACHED
        att.container_id = None
        att.interface_host = None
        att.interface_container = None
        att.updated_at = now
        self._session.add(att)
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise WorkspaceDetachError(f"failed to persist workspace detach: {e}") from e
        self._session.refresh(att)
        return DetachWorkspaceResult(
            detached=True,
            status=TopologyAttachmentStatus.DETACHED,
            workspace_id=workspace_id,
            workspace_ip=prev_ip,
            released_ip=False,
        )

    def release_workspace_ip_lease(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> bool:
        node_id = node_id.strip()[:128]
        stmt = select(IpAllocation).where(
            IpAllocation.topology_id == topology_id,
            IpAllocation.node_id == node_id,
            IpAllocation.workspace_id == workspace_id,
            IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
        )
        row = self._session.exec(stmt).first()
        if row is None:
            return False
        row.released_at = datetime.now(timezone.utc)
        self._session.add(row)
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise WorkspaceIPAllocationError(f"failed to release workspace IP lease: {e}") from e
        return True

    def delete_topology(self, *, topology_id: int, node_id: str) -> None:
        if not node_id or not node_id.strip():
            raise TopologyDeleteError("node_id is required")
        node_id = node_id.strip()[:128]

        blocking_attachment = select(TopologyAttachment).where(
            TopologyAttachment.topology_id == topology_id,
            TopologyAttachment.node_id == node_id,
            TopologyAttachment.status != TopologyAttachmentStatus.DETACHED,
        )
        if self._session.exec(blocking_attachment).first() is not None:
            raise TopologyDeleteError(
                "cannot delete topology runtime: non-DETACHED attachments remain "
                "(detach workspaces first)",
            )

        rt_stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        runtime = self._session.exec(rt_stmt).first()
        if runtime is None:
            # Idempotent cleanup: if only DETACHED rows remain, remove them (no IP state touched).
            att_delete_stmt = select(TopologyAttachment).where(
                TopologyAttachment.topology_id == topology_id,
                TopologyAttachment.node_id == node_id,
            )
            for att in self._session.exec(att_delete_stmt).all():
                self._session.delete(att)
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
            return

        bridge_name = str(runtime.bridge_name).strip() if runtime.bridge_name else ""

        att_delete_stmt = select(TopologyAttachment).where(
            TopologyAttachment.topology_id == topology_id,
            TopologyAttachment.node_id == node_id,
        )
        for att in self._session.exec(att_delete_stmt).all():
            self._session.delete(att)
        self._session.delete(runtime)
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise TopologyDeleteError(f"failed to persist topology deletion: {e}") from e

        # Remove Linux bridge after DB commit so control-plane state is not lost if bridge removal fails.
        if self._apply_linux_bridge and bridge_name:
            from .system.bridge_ops import remove_bridge_if_exists
            from .system.command_runner import CommandRunner

            r = self._command_runner or CommandRunner()
            try:
                remove_bridge_if_exists(bridge_name, runner=r)
            except ValueError as e:
                raise TopologyDeleteError(f"cannot delete bridge {bridge_name!r}: {e}") from e
            except RuntimeError as e:
                raise TopologyDeleteError(f"linux bridge removal failed: {e}") from e

    def check_topology(self, *, topology_id: int, node_id: str) -> CheckTopologyResult:
        node_id = node_id.strip()[:128]
        stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        row = self._session.exec(stmt).first()
        if row is None:
            return CheckTopologyResult(
                healthy=False,
                status=TopologyRuntimeStatus.FAILED,
                issues=("db: topology runtime not found for this topology and node",),
                topology_runtime_id=None,
                bridge_name=None,
                cidr=None,
                gateway_ip=None,
            )
        db_issues: list[str] = []
        if not (row.bridge_name and str(row.bridge_name).strip()):
            db_issues.append("db: bridge_name is not set")
        if not (row.cidr and str(row.cidr).strip()):
            db_issues.append("db: cidr is not set")
        if not (row.gateway_ip and str(row.gateway_ip).strip()):
            db_issues.append("db: gateway_ip is not set")
        if row.status != TopologyRuntimeStatus.READY:
            db_issues.append(
                f"db: runtime status is {row.status.value}, expected READY for V1 healthy",
            )
        assert row.topology_runtime_id is not None
        runtime_issues = _runtime_consistency_issues(cidr=row.cidr, gateway_ip=row.gateway_ip)

        linux_issues: list[str] = []
        if self._apply_linux_bridge:
            from .system.bridge_ops import (
                check_bridge_exists,
                check_bridge_has_ipv4_address,
                check_bridge_link_up,
            )
            from .system.command_runner import CommandRunner

            r = self._command_runner or CommandRunner()
            bn = str(row.bridge_name).strip() if row.bridge_name else ""
            if bn:
                try:
                    if not check_bridge_exists(bn, runner=r):
                        linux_issues.append("linux: bridge network device not found on host")
                    elif not check_bridge_link_up(bn, runner=r):
                        linux_issues.append("linux: bridge interface is not UP")
                    elif row.cidr and str(row.cidr).strip() and row.gateway_ip and str(row.gateway_ip).strip():
                        if not check_bridge_has_ipv4_address(
                            bn,
                            str(row.gateway_ip).strip(),
                            str(row.cidr).strip(),
                            runner=r,
                        ):
                            linux_issues.append(
                                "linux: bridge lacks expected IPv4/gateway address from topology runtime",
                            )
                except ValueError as e:
                    linux_issues.append(f"linux: bridge check skipped or invalid ({e})")
                except RuntimeError as e:
                    linux_issues.append(f"linux: bridge check failed ({e})")

        # Keep stable ordering: DB -> runtime consistency -> linux
        issues = tuple(db_issues + runtime_issues + linux_issues)
        healthy = len(issues) == 0
        return CheckTopologyResult(
            healthy=healthy,
            status=row.status,
            issues=issues,
            topology_runtime_id=row.topology_runtime_id,
            bridge_name=row.bridge_name,
            cidr=row.cidr,
            gateway_ip=row.gateway_ip,
        )

    def check_attachment(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> CheckAttachmentResult:
        node_id = node_id.strip()[:128]
        att_stmt = select(TopologyAttachment).where(
            TopologyAttachment.topology_id == topology_id,
            TopologyAttachment.node_id == node_id,
            TopologyAttachment.workspace_id == workspace_id,
        )
        att = self._session.exec(att_stmt).first()
        if att is None:
            return CheckAttachmentResult(
                healthy=False,
                status=TopologyAttachmentStatus.DETACHED,
                issues=("db: topology attachment not found",),
                attachment_id=None,
            )
        db_issues: list[str] = []
        if att.status != TopologyAttachmentStatus.ATTACHED:
            db_issues.append(
                f"db: attachment status is {att.status.value}, expected ATTACHED for healthy V1",
            )
        if not (att.container_id and str(att.container_id).strip()):
            db_issues.append("db: container_id is not set")
        if not (att.workspace_ip and str(att.workspace_ip).strip()):
            db_issues.append("db: workspace_ip is not set")
        if not (att.bridge_name and str(att.bridge_name).strip()):
            db_issues.append("db: bridge_name is not set")
        if not (att.gateway_ip and str(att.gateway_ip).strip()):
            db_issues.append("db: gateway_ip is not set")

        alloc_stmt = select(IpAllocation).where(
            IpAllocation.topology_id == topology_id,
            IpAllocation.node_id == node_id,
            IpAllocation.workspace_id == workspace_id,
            IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
        )
        alloc = self._session.exec(alloc_stmt).first()
        if alloc is None:
            db_issues.append("db: no active IpAllocation for this workspace")
        elif att.workspace_ip and alloc.ip != att.workspace_ip:
            db_issues.append(
                "db: workspace_ip on attachment does not match active lease ip",
            )

        # Runtime consistency checks (do not require Linux access).
        runtime_stmt = select(TopologyRuntime).where(
            TopologyRuntime.topology_id == topology_id,
            TopologyRuntime.node_id == node_id,
        )
        runtime = self._session.exec(runtime_stmt).first()
        runtime_issues: list[str] = []
        if runtime is None:
            runtime_issues.append("runtime: topology runtime not found for this node")
        else:
            runtime_issues.extend(_runtime_consistency_issues(cidr=runtime.cidr, gateway_ip=runtime.gateway_ip))
            if (
                att.workspace_ip
                and runtime.cidr
                and str(runtime.cidr).strip()
                and not any(i.startswith("runtime: cidr is") for i in runtime_issues)
            ):
                try:
                    net_any = ipaddress.ip_network(str(runtime.cidr).strip(), strict=False)
                    ip_any = ipaddress.ip_address(str(att.workspace_ip).strip())
                    if isinstance(net_any, ipaddress.IPv4Network) and isinstance(ip_any, ipaddress.IPv4Address):
                        if ip_any not in net_any:
                            runtime_issues.append("runtime: workspace_ip is not within runtime cidr")
                    else:
                        runtime_issues.append("runtime: workspace_ip/cidr family mismatch")
                except ValueError as e:
                    runtime_issues.append(f"runtime: workspace_ip/cidr validation failed ({e})")

        ws_ip = att.workspace_ip
        internal = f"{ws_ip}:{WORKSPACE_IDE_CONTAINER_PORT}" if ws_ip else None

        linux_issues: list[str] = []
        if self._apply_linux_attachment:
            db_ok_for_link = (
                att.status == TopologyAttachmentStatus.ATTACHED
                and (att.container_id and str(att.container_id).strip())
                and (att.workspace_ip and str(att.workspace_ip).strip())
                and (att.bridge_name and str(att.bridge_name).strip())
                and alloc is not None
                and alloc.ip == att.workspace_ip
            )
            if db_ok_for_link:
                from .system.attachment_ops import (
                    check_host_veth_enslaved_to_bridge,
                    check_interface_exists,
                )
                from .system.command_runner import CommandRunner

                r = self._command_runner or CommandRunner()
                host_if = (
                    str(att.interface_host).strip()
                    if att.interface_host and str(att.interface_host).strip()
                    else _veth_pair_names(topology_id, node_id, workspace_id)[0]
                )
                brn = str(att.bridge_name).strip()
                try:
                    if not check_interface_exists(host_if, runner=r):
                        linux_issues.append(
                            "linux: host-side veth for workspace attachment not found on node",
                        )
                    elif not check_host_veth_enslaved_to_bridge(host_if, brn, runner=r):
                        linux_issues.append(
                            "linux: host veth is not enslaved to expected topology bridge",
                        )
                except ValueError as e:
                    linux_issues.append(f"linux: host interface check failed ({e})")
                except RuntimeError as e:
                    linux_issues.append(f"linux: host interface check failed ({e})")

        issues = tuple(db_issues + runtime_issues + linux_issues)
        healthy = len(issues) == 0
        assert att.attachment_id is not None
        return CheckAttachmentResult(
            healthy=healthy,
            status=att.status,
            workspace_ip=ws_ip,
            internal_endpoint=internal,
            issues=issues,
            attachment_id=att.attachment_id,
        )
