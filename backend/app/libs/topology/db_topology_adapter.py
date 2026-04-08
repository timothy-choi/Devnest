"""
DB-backed ``TopologyAdapter`` (V1): control-plane state for node bridge, IP leases, attachments.

Linux bridge/iptables and ``netns_ref`` consumption are extension points; this slice persists rows only.
"""

from __future__ import annotations

import hashlib
import ipaddress
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT

from .errors import (
    TopologyRuntimeCreateError,
    TopologyRuntimeNotFoundError,
    WorkspaceAttachmentError,
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
    EnsureNodeTopologyResult,
)

_V1_MODE = "node_bridge"
_DEFAULT_CIDR = "10.77.0.0/24"


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


def _node_bridge_plan(topology: Topology, node_id: str) -> tuple[str, str, str]:
    """
    Return (bridge_name, cidr, gateway_ip) for V1 node_bridge.

    Optional ``spec_json`` keys: ``bridge_name``, ``cidr``, ``gateway_ip``, ``mode``.
    """
    spec = topology.spec_json if isinstance(topology.spec_json, dict) else {}
    mode = _spec_mode(spec)
    if mode != _V1_MODE:
        raise TopologyRuntimeCreateError(
            f"topology V1 supports mode {_V1_MODE!r} only; got {mode!r}",
        )
    cidr = spec.get("cidr")
    if not isinstance(cidr, str) or not cidr.strip():
        cidr = _DEFAULT_CIDR
    else:
        cidr = cidr.strip()
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise TopologyRuntimeCreateError(f"invalid cidr in topology spec: {cidr!r}") from e
    if network.version != 4:
        raise TopologyRuntimeCreateError("V1 IP allocation supports IPv4 CIDR only")
    hosts = list(network.hosts())
    if not hosts:
        raise TopologyRuntimeCreateError(f"no usable hosts in CIDR {cidr!r}")
    gw_raw = spec.get("gateway_ip")
    if isinstance(gw_raw, str) and gw_raw.strip():
        try:
            gw = ipaddress.ip_address(gw_raw.strip())
        except ValueError as e:
            raise TopologyRuntimeCreateError(f"invalid gateway_ip in spec: {gw_raw!r}") from e
        if gw not in network:
            raise TopologyRuntimeCreateError(f"gateway_ip {gw} not in network {network}")
        gateway_ip = str(gw)
    else:
        gateway_ip = str(hosts[0])
    bridge_raw = spec.get("bridge_name")
    if isinstance(bridge_raw, str) and bridge_raw.strip():
        bridge_name = bridge_raw.strip()[:64]
    else:
        bridge_name = _bridge_name_for(topology.topology_id or 0, node_id)
    return bridge_name, cidr, gateway_ip


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


def _iter_candidate_workspace_hosts(cidr: str, gateway_ip: str) -> list[ipaddress.IPv4Address]:
    """Usable host addresses: network hosts minus gateway (network/broadcast already excluded by .hosts())."""
    net = ipaddress.ip_network(cidr, strict=False)
    gw = _gateway_as_address(cidr, gateway_ip)
    out: list[ipaddress.IPv4Address] = []
    for h in net.hosts():
        if not isinstance(h, ipaddress.IPv4Address):
            continue
        if h == gw:
            continue
        out.append(h)
    return out


class DbTopologyAdapter(TopologyAdapter):
    """
    Persist ``TopologyRuntime``, ``IpAllocation``, and ``TopologyAttachment`` for V1 node_bridge.

    Pass a request-scoped or unit-of-work ``Session``; each public method commits on success.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

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
        if existing is not None:
            return _runtime_to_ensure_result(existing)

        try:
            bridge_name, cidr, gateway_ip = _node_bridge_plan(topo, node_id)
        except TopologyRuntimeCreateError:
            self._session.rollback()
            raise
        now = datetime.now(timezone.utc)
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
        try:
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise TopologyRuntimeCreateError(f"failed to persist topology runtime: {e}") from e
        self._session.refresh(row)
        assert row.topology_runtime_id is not None
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
        chosen: ipaddress.IPv4Address | None = None
        for h in candidates:
            if h not in used_ips:
                chosen = h
                break
        if chosen is None:
            raise WorkspaceIPAllocationError("no free IPv4 addresses in topology CIDR")

        now = datetime.now(timezone.utc)
        ip_str = str(chosen)
        try:
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
            self._session.commit()
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
        _ = netns_ref  # V1 control-plane only; Linux join deferred.
        if not container_id or not container_id.strip():
            raise WorkspaceAttachmentError("container_id is required")
        container_id = container_id.strip()[:128]
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

        att_stmt = select(TopologyAttachment).where(
            TopologyAttachment.topology_id == topology_id,
            TopologyAttachment.node_id == node_id,
            TopologyAttachment.workspace_id == workspace_id,
        )
        att = self._session.exec(att_stmt).first()
        now = datetime.now(timezone.utc)
        internal_endpoint = f"{workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        try:
            if att is None:
                att = TopologyAttachment(
                    topology_id=topology_id,
                    node_id=node_id,
                    workspace_id=workspace_id,
                    container_id=container_id,
                    status=TopologyAttachmentStatus.ATTACHED,
                    workspace_ip=workspace_ip,
                    bridge_name=runtime.bridge_name,
                    gateway_ip=runtime.gateway_ip,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(att)
            else:
                att.container_id = container_id
                att.status = TopologyAttachmentStatus.ATTACHED
                att.workspace_ip = workspace_ip
                att.bridge_name = runtime.bridge_name
                att.gateway_ip = runtime.gateway_ip
                att.updated_at = now
                self._session.add(att)
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise WorkspaceAttachmentError(f"failed to persist topology attachment: {e}") from e
        self._session.refresh(att)
        assert att.attachment_id is not None
        return AttachWorkspaceResult(
            attachment_id=att.attachment_id,
            workspace_ip=workspace_ip,
            bridge_name=runtime.bridge_name,
            gateway_ip=runtime.gateway_ip,
            internal_endpoint=internal_endpoint,
        )

    def detach_workspace(self, *, topology_id: int, node_id: str, workspace_id: int) -> None:
        raise NotImplementedError("detach_workspace is deferred to a later topology step")

    def delete_topology(self, *, topology_id: int, node_id: str) -> None:
        raise NotImplementedError("delete_topology is deferred to a later topology step")

    def check_topology(self, *, topology_id: int, node_id: str) -> CheckTopologyResult:
        raise NotImplementedError("check_topology is deferred to a later topology step")

    def check_attachment(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> CheckAttachmentResult:
        raise NotImplementedError("check_attachment is deferred to a later topology step")
