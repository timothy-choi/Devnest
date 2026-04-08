"""Unit tests: ``DbTopologyAdapter`` V1 slice (SQLite, no Postgres)."""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.errors import (
    TopologyRuntimeCreateError,
    TopologyRuntimeNotFoundError,
    WorkspaceAttachmentError,
    WorkspaceIPAllocationError,
)
from app.libs.topology.models import (
    IpAllocation,
    Topology,
    TopologyAttachment,
    TopologyRuntime,
)
from app.libs.topology.models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus


@pytest.fixture
def topo_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _insert_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name="unit-topo", version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


class TestEnsureNodeTopology:
    def test_creates_runtime_when_missing(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        out = adapter.ensure_node_topology(topology_id=tid, node_id="node-a")
        assert out.topology_runtime_id is not None
        assert out.status == TopologyRuntimeStatus.READY
        assert out.cidr == "10.77.0.0/24"
        assert out.gateway_ip == "10.77.0.1"
        assert out.bridge_name is not None
        row = topo_session.get(TopologyRuntime, out.topology_runtime_id)
        assert row is not None
        assert row.topology_id == tid
        assert row.node_id == "node-a"

    def test_idempotent_returns_same_runtime(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        first = adapter.ensure_node_topology(topology_id=tid, node_id="node-b")
        second = adapter.ensure_node_topology(topology_id=tid, node_id="node-b")
        assert first.topology_runtime_id == second.topology_runtime_id
        assert first.cidr == second.cidr
        rows = topo_session.exec(
            select(TopologyRuntime).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == "node-b",
            ),
        ).all()
        assert len(rows) == 1

    def test_raises_when_topology_missing(self, topo_session: Session) -> None:
        adapter = DbTopologyAdapter(topo_session)
        with pytest.raises(TopologyRuntimeCreateError, match="not found"):
            adapter.ensure_node_topology(topology_id=99999, node_id="n")

    def test_raises_on_unsupported_mode(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session, spec={"mode": "overlay"})
        adapter = DbTopologyAdapter(topo_session)
        with pytest.raises(TopologyRuntimeCreateError, match="node_bridge"):
            adapter.ensure_node_topology(topology_id=tid, node_id="n")


class TestAllocateWorkspaceIP:
    def test_reuses_active_lease(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.1.0/24", "gateway_ip": "10.77.1.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        a1 = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=100)
        a2 = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=100)
        assert a1.workspace_ip == a2.workspace_ip
        assert a2.leased_existing is True
        assert a1.leased_existing is False

    def test_skips_gateway_and_no_duplicates(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.2.0/24", "gateway_ip": "10.77.2.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        w1 = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=1)
        w2 = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=2)
        assert w1.workspace_ip == "10.77.2.2"
        assert w2.workspace_ip == "10.77.2.3"
        assert "10.77.2.1" not in (w1.workspace_ip, w2.workspace_ip)
        ips = topo_session.exec(
            select(IpAllocation.ip).where(
                IpAllocation.topology_id == tid,
                IpAllocation.node_id == "n1",
                IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
            ),
        ).all()
        assert len(ips) == len(set(ips))

    def test_deterministic_order(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.3.0/24", "gateway_ip": "10.77.3.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        x = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=50)
        y = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=51)
        assert x.workspace_ip < y.workspace_ip

    def test_raises_without_runtime(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        with pytest.raises(TopologyRuntimeNotFoundError):
            adapter.allocate_workspace_ip(topology_id=tid, node_id="missing", workspace_id=1)

    def test_exhaustion_when_only_one_host_available(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.99.0/30", "gateway_ip": "10.77.99.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=1)
        with pytest.raises(WorkspaceIPAllocationError, match="no free"):
            adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=2)


class TestAttachWorkspace:
    def test_creates_attachment_and_internal_endpoint(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.4.0/24", "gateway_ip": "10.77.4.1", "bridge_name": "br-unit"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=7)
        res = adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=7,
            container_id="cid-aaa",
            netns_ref="/proc/1/ns/net",
            workspace_ip=ip.workspace_ip,
        )
        assert res.internal_endpoint == f"{ip.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        att = topo_session.get(TopologyAttachment, res.attachment_id)
        assert att is not None
        assert att.topology_id == tid
        assert att.node_id == "n1"
        assert att.workspace_id == 7
        assert att.container_id == "cid-aaa"
        assert att.workspace_ip == ip.workspace_ip
        assert att.bridge_name == "br-unit"
        assert att.gateway_ip == "10.77.4.1"
        assert att.status == TopologyAttachmentStatus.ATTACHED

    def test_updates_existing_attachment(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.5.0/24", "gateway_ip": "10.77.5.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=8)
        r1 = adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=8,
            container_id="old-cid",
            netns_ref="/proc/1/ns/net",
            workspace_ip=ip.workspace_ip,
        )
        r2 = adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=8,
            container_id="new-cid",
            netns_ref="/proc/2/ns/net",
            workspace_ip=ip.workspace_ip,
        )
        assert r1.attachment_id == r2.attachment_id
        row = topo_session.get(TopologyAttachment, r1.attachment_id)
        assert row is not None
        assert row.container_id == "new-cid"

    def test_rejects_mismatched_workspace_ip(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=9)
        with pytest.raises(WorkspaceAttachmentError, match="allocate_workspace_ip"):
            adapter.attach_workspace(
                topology_id=tid,
                node_id="n1",
                workspace_id=9,
                container_id="x",
                netns_ref="/p",
                workspace_ip="10.0.0.99",
            )
        assert ip.workspace_ip != "10.0.0.99"
