"""Unit tests: ``DbTopologyAdapter.run_topology_janitor`` (SQLite)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.libs.topology import DbTopologyAdapter
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


def _tid(session: Session) -> int:
    t = Topology(name="janitor-topo", version="v1", spec_json={})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


def _runtime(session: Session, *, topology_id: int, node_id: str = "n1") -> None:
    r = TopologyRuntime(
        topology_id=topology_id,
        node_id=node_id,
        bridge_name="brx",
        cidr="10.200.0.0/24",
        gateway_ip="10.200.0.1",
        status=TopologyRuntimeStatus.READY,
    )
    session.add(r)
    session.commit()


class TestTopologyJanitor:
    def test_releases_orphan_active_lease_without_attached_row(self, topo_session: Session) -> None:
        tid = _tid(topo_session)
        _runtime(topo_session, topology_id=tid)
        lease = IpAllocation(
            node_id="n1",
            topology_id=tid,
            workspace_id=701,
            ip="10.200.0.20",
        )
        topo_session.add(lease)
        topo_session.commit()

        adapter = DbTopologyAdapter(topo_session, apply_linux_bridge=False, apply_linux_attachment=False)
        jr = adapter.run_topology_janitor(topology_id=tid, node_id="n1", stale_attaching_seconds=600)
        assert jr.orphan_ip_leases_released == 1
        row = topo_session.exec(
            select(IpAllocation).where(IpAllocation.workspace_id == 701),
        ).first()
        assert row is not None
        assert row.released_at is not None

    def test_detaches_stale_attaching_and_releases_ip(self, topo_session: Session) -> None:
        tid = _tid(topo_session)
        _runtime(topo_session, topology_id=tid)
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        att = TopologyAttachment(
            topology_id=tid,
            node_id="n1",
            workspace_id=702,
            status=TopologyAttachmentStatus.ATTACHING,
            workspace_ip="10.200.0.21",
            created_at=old,
            updated_at=old,
        )
        lease = IpAllocation(
            node_id="n1",
            topology_id=tid,
            workspace_id=702,
            ip="10.200.0.21",
        )
        topo_session.add(att)
        topo_session.add(lease)
        topo_session.commit()

        adapter = DbTopologyAdapter(topo_session, apply_linux_bridge=False, apply_linux_attachment=False)
        jr = adapter.run_topology_janitor(topology_id=tid, node_id="n1", stale_attaching_seconds=300)
        assert jr.stale_attachments_cleaned == 1
        att2 = topo_session.exec(
            select(TopologyAttachment).where(TopologyAttachment.workspace_id == 702),
        ).first()
        assert att2 is not None
        assert att2.status == TopologyAttachmentStatus.DETACHED
        alloc = topo_session.exec(
            select(IpAllocation).where(IpAllocation.workspace_id == 702),
        ).first()
        assert alloc is not None
        assert alloc.released_at is not None

    def test_idempotent_second_pass_is_quiet(self, topo_session: Session) -> None:
        tid = _tid(topo_session)
        _runtime(topo_session, topology_id=tid)
        adapter = DbTopologyAdapter(topo_session, apply_linux_bridge=False, apply_linux_attachment=False)
        j1 = adapter.run_topology_janitor(topology_id=tid, node_id="n1")
        j2 = adapter.run_topology_janitor(topology_id=tid, node_id="n1")
        assert j1 == j2
