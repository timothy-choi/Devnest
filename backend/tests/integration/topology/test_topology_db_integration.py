"""Integration tests: topology V1 persistence on PostgreSQL (worker-isolated DB).

Live bridge/veth/netns behavior is covered under ``tests/system/topology/`` (Linux + Docker).
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import IpAllocation, Topology, TopologyAttachment, TopologyRuntime
from app.libs.topology.models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus


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


def test_detach_persists_detached_ip_lease_stable(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.5.0/24", "gateway_ip": "10.88.5.1", "bridge_name": "br-det"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-det")
    ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-det", workspace_id=800)
    adapter.attach_workspace(
        topology_id=tid,
        node_id="n-det",
        workspace_id=800,
        container_id="c-det",
        netns_ref="/proc/1/ns/net",
        workspace_ip=ip.workspace_ip,
    )
    out = adapter.detach_workspace(topology_id=tid, node_id="n-det", workspace_id=800)
    assert out.detached is True
    assert out.released_ip is False
    row = db_session.exec(
        select(TopologyAttachment).where(
            TopologyAttachment.topology_id == tid,
            TopologyAttachment.workspace_id == 800,
        ),
    ).first()
    assert row is not None
    assert row.status == TopologyAttachmentStatus.DETACHED
    assert row.container_id is None
    lease = db_session.exec(
        select(IpAllocation).where(
            IpAllocation.topology_id == tid,
            IpAllocation.workspace_id == 800,
        ),
    ).first()
    assert lease is not None
    assert lease.released_at is None
    assert lease.ip == ip.workspace_ip


def test_check_topology_reflects_persisted_runtime(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.6.0/24", "gateway_ip": "10.88.6.1"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-ct")
    res = adapter.check_topology(topology_id=tid, node_id="n-ct")
    assert res.healthy is True
    assert res.cidr == "10.88.6.0/24"
    rt = db_session.exec(
        select(TopologyRuntime).where(
            TopologyRuntime.topology_id == tid,
            TopologyRuntime.node_id == "n-ct",
        ),
    ).first()
    assert rt is not None
    assert res.topology_runtime_id == rt.topology_runtime_id


def test_check_topology_unhealthy_when_missing_runtime(db_session: Session) -> None:
    tid = _seed_topology(db_session)
    adapter = DbTopologyAdapter(db_session)
    res = adapter.check_topology(topology_id=tid, node_id="ghost-node")
    assert res.healthy is False
    assert res.topology_runtime_id is None
    assert res.status == TopologyRuntimeStatus.FAILED


def test_check_attachment_persisted_and_internal_endpoint(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.88.7.0/24", "gateway_ip": "10.88.7.1", "bridge_name": "br-ca"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-ca")
    ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-ca", workspace_id=900)
    att = adapter.attach_workspace(
        topology_id=tid,
        node_id="n-ca",
        workspace_id=900,
        container_id="c-ca",
        netns_ref="/ns/ca",
        workspace_ip=ip.workspace_ip,
    )
    chk = adapter.check_attachment(topology_id=tid, node_id="n-ca", workspace_id=900)
    assert chk.healthy is True
    assert chk.attachment_id == att.attachment_id
    assert chk.internal_endpoint == f"{ip.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
