"""Integration tests: topology V1 persistence on PostgreSQL (worker-isolated DB)."""

from __future__ import annotations

from sqlmodel import Session, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import IpAllocation, Topology, TopologyAttachment, TopologyRuntime
from app.libs.topology.models.enums import TopologyAttachmentStatus


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name="int-topo", version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


def test_ensure_node_topology_single_row_idempotent(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.0.0/24", "gateway_ip": "10.88.0.1"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="gw-node-1")
    adapter.ensure_node_topology(topology_id=tid, node_id="gw-node-1")
    rows = db_session.exec(
        select(TopologyRuntime).where(
            TopologyRuntime.topology_id == tid,
            TopologyRuntime.node_id == "gw-node-1",
        ),
    ).all()
    assert len(rows) == 1
    assert rows[0].cidr == "10.88.0.0/24"


def test_allocate_persists_lease_reuse_and_unique_ips(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.1.0/24", "gateway_ip": "10.88.1.1"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-alpha")
    first = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-alpha", workspace_id=501)
    second = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-alpha", workspace_id=501)
    assert first.workspace_ip == second.workspace_ip
    assert second.leased_existing is True
    other = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-alpha", workspace_id=502)
    assert other.workspace_ip != first.workspace_ip
    assert "10.88.1.1" not in (first.workspace_ip, other.workspace_ip)
    persisted = db_session.exec(
        select(IpAllocation).where(
            IpAllocation.topology_id == tid,
            IpAllocation.node_id == "n-alpha",
            IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
        ),
    ).all()
    assert len(persisted) == 2
    assert len({p.ip for p in persisted}) == 2


def test_gateway_not_leased(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.2.0/24", "gateway_ip": "10.88.2.100"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-gw")
    lease = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-gw", workspace_id=1)
    assert lease.workspace_ip != "10.88.2.100"


def test_attach_persists_and_internal_endpoint(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.3.0/24", "gateway_ip": "10.88.3.1", "bridge_name": "br-int"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-attach")
    ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-attach", workspace_id=600)
    out = adapter.attach_workspace(
        topology_id=tid,
        node_id="n-attach",
        workspace_id=600,
        container_id="container-int-1",
        netns_ref="/proc/999/ns/net",
        workspace_ip=ip.workspace_ip,
    )
    assert out.internal_endpoint == f"{ip.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
    row = db_session.get(TopologyAttachment, out.attachment_id)
    assert row is not None
    assert row.workspace_ip == ip.workspace_ip
    assert row.topology_id == tid
    assert row.node_id == "n-attach"
    assert row.workspace_id == 600
    assert row.container_id == "container-int-1"
    assert row.bridge_name == "br-int"
    assert row.gateway_ip == "10.88.3.1"
    assert row.status == TopologyAttachmentStatus.ATTACHED


def test_attach_twice_stable_attachment_id(db_session: Session) -> None:
    tid = _seed_topology(db_session, spec={"cidr": "10.88.4.0/24", "gateway_ip": "10.88.4.1"})
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-stable")
    ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-stable", workspace_id=700)
    r1 = adapter.attach_workspace(
        topology_id=tid,
        node_id="n-stable",
        workspace_id=700,
        container_id="c1",
        netns_ref="/ns/1",
        workspace_ip=ip.workspace_ip,
    )
    r2 = adapter.attach_workspace(
        topology_id=tid,
        node_id="n-stable",
        workspace_id=700,
        container_id="c2",
        netns_ref="/ns/2",
        workspace_ip=ip.workspace_ip,
    )
    assert r1.attachment_id == r2.attachment_id
    assert r2.internal_endpoint == f"{ip.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
    assert db_session.get(TopologyAttachment, r1.attachment_id).container_id == "c2"
