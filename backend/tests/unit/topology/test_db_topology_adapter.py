"""Unit tests: ``DbTopologyAdapter`` V1 slice (SQLite, no Postgres)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.db_topology_adapter import _veth_pair_names
from app.libs.topology.errors import (
    TopologyDeleteError,
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


class TestEnsureNodeTopologyLinuxBridge:
    """``apply_linux_bridge=True`` bypasses autouse SKIP when passed explicitly."""

    def test_records_degraded_on_bridge_os_error(self, topo_session: Session) -> None:
        class FailRunner:
            def run(self, cmd: list[str]) -> str:
                raise RuntimeError("simulated ip failure")

        tid = _insert_topology(
            topo_session,
            spec={
                "bridge_name": "dnfail1",
                "cidr": "10.2.0.0/24",
                "gateway_ip": "10.2.0.1",
            },
        )
        adapter = DbTopologyAdapter(
            topo_session,
            command_runner=FailRunner(),
            apply_linux_bridge=True,
        )
        out = adapter.ensure_node_topology(topology_id=tid, node_id="n-fail")
        assert out.status == TopologyRuntimeStatus.DEGRADED
        row = topo_session.get(TopologyRuntime, out.topology_runtime_id)
        assert row is not None
        assert row.last_error_code == "BRIDGE_OS"
        assert row.last_error_message is not None
        assert "simulated" in row.last_error_message

    def test_second_ensure_still_syncs_bridge(self, topo_session: Session) -> None:
        calls: list[list[str]] = []

        class RecRunner:
            def run(self, cmd: list[str]) -> str:
                calls.append(list(cmd))
                return ""

        tid = _insert_topology(
            topo_session,
            spec={
                "bridge_name": "dnidmp1",
                "cidr": "10.3.0.0/24",
                "gateway_ip": "10.3.0.1",
            },
        )
        adapter = DbTopologyAdapter(
            topo_session,
            command_runner=RecRunner(),
            apply_linux_bridge=True,
        )
        adapter.ensure_node_topology(topology_id=tid, node_id="n-dup")
        n1 = len(calls)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-dup")
        assert len(calls) > n1

    def test_incomplete_runtime_degraded_without_ip(self, topo_session: Session) -> None:
        class FailRunner:
            def run(self, cmd: list[str]) -> str:
                raise AssertionError(f"unexpected ip call: {cmd}")

        tid = _insert_topology(topo_session)
        now = datetime.now(timezone.utc)
        row = TopologyRuntime(
            topology_id=tid,
            node_id="bad",
            status=TopologyRuntimeStatus.READY,
            bridge_name="brincompl",
            cidr="10.0.0.0/24",
            gateway_ip=None,
            managed_by_agent=True,
            created_at=now,
            updated_at=now,
        )
        topo_session.add(row)
        topo_session.commit()
        topo_session.refresh(row)

        adapter = DbTopologyAdapter(
            topo_session,
            command_runner=FailRunner(),
            apply_linux_bridge=True,
        )
        out = adapter.ensure_node_topology(topology_id=tid, node_id="bad")
        assert out.status == TopologyRuntimeStatus.DEGRADED
        assert out.topology_runtime_id == row.topology_runtime_id
        r2 = topo_session.get(TopologyRuntime, row.topology_runtime_id)
        assert r2 is not None
        assert r2.last_error_code == "INCOMPLETE_RUNTIME"


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

    def test_raises_when_runtime_not_ready(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.88.0/24", "gateway_ip": "10.77.88.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        out = adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        row = topo_session.get(TopologyRuntime, out.topology_runtime_id)
        assert row is not None
        row.status = TopologyRuntimeStatus.DEGRADED
        topo_session.add(row)
        topo_session.commit()
        with pytest.raises(WorkspaceIPAllocationError, match="not READY"):
            adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=1)

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

    def test_rejects_invalid_netns_ref(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session, apply_linux_attachment=True)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=91)
        with pytest.raises(WorkspaceAttachmentError, match="unsupported netns_ref"):
            adapter.attach_workspace(
                topology_id=tid,
                node_id="n1",
                workspace_id=91,
                container_id="c",
                netns_ref="/not/proc",
                workspace_ip=ip.workspace_ip,
            )

    def test_linux_attach_failure_persists_failed(self, topo_session: Session) -> None:
        class FailRunner:
            def run(self, cmd: list[str]) -> str:
                raise RuntimeError("boom")

        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.40.0/24", "gateway_ip": "10.77.40.1", "bridge_name": "br-fail"},
        )
        adapter = DbTopologyAdapter(
            topo_session,
            command_runner=FailRunner(),
            apply_linux_attachment=True,
        )
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=40)
        with pytest.raises(WorkspaceAttachmentError, match="linux attach failed"):
            adapter.attach_workspace(
                topology_id=tid,
                node_id="n1",
                workspace_id=40,
                container_id="c-fail",
                netns_ref="/proc/1/ns/net",
                workspace_ip=ip.workspace_ip,
            )
        row = topo_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.workspace_id == 40,
            ),
        ).first()
        assert row is not None
        assert row.status == TopologyAttachmentStatus.FAILED


class TestDetachWorkspace:
    def test_idempotent_when_no_attachment(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        out = adapter.detach_workspace(topology_id=tid, node_id="n1", workspace_id=999)
        assert out.detached is False
        assert out.status == TopologyAttachmentStatus.DETACHED
        assert out.workspace_id == 999
        assert out.workspace_ip is None
        assert out.released_ip is False

    def test_sets_detached_and_clears_container_id(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.10.0/24", "gateway_ip": "10.77.10.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=20)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=20,
            container_id="cid-live",
            netns_ref="/ns/x",
            workspace_ip=ip.workspace_ip,
        )
        out = adapter.detach_workspace(topology_id=tid, node_id="n1", workspace_id=20)
        assert out.detached is True
        assert out.released_ip is False
        assert out.workspace_ip == ip.workspace_ip
        row = topo_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.node_id == "n1",
                TopologyAttachment.workspace_id == 20,
            ),
        ).first()
        assert row is not None
        assert row.status == TopologyAttachmentStatus.DETACHED
        assert row.container_id is None
        assert row.interface_host is None
        assert row.interface_container is None

    def test_linux_detach_runs_ip_when_apply_enabled(self, topo_session: Session) -> None:
        calls: list[list[str]] = []

        class RecRunner:
            def run(self, cmd: list[str]) -> str:
                calls.append(list(cmd))
                return ""

        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.50.0/24", "gateway_ip": "10.77.50.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=50)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=50,
            container_id="c",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        row = topo_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.workspace_id == 50,
            ),
        ).first()
        assert row is not None
        assert row.interface_host

        detach_ad = DbTopologyAdapter(
            topo_session,
            command_runner=RecRunner(),
            apply_linux_attachment=True,
        )
        detach_ad.detach_workspace(topology_id=tid, node_id="n1", workspace_id=50)
        assert calls and calls[0][0] == "ip" and "link" in calls[0]
        row2 = topo_session.get(TopologyAttachment, row.attachment_id)
        assert row2 is not None
        assert row2.interface_host is None
        assert row2.interface_container is None

    def test_ip_lease_not_released_on_detach(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session, spec={"cidr": "10.77.11.0/24", "gateway_ip": "10.77.11.1"})
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=21)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=21,
            container_id="c",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        adapter.detach_workspace(topology_id=tid, node_id="n1", workspace_id=21)
        lease = topo_session.exec(
            select(IpAllocation).where(
                IpAllocation.topology_id == tid,
                IpAllocation.workspace_id == 21,
            ),
        ).first()
        assert lease is not None
        assert lease.released_at is None
        assert lease.ip == ip.workspace_ip

    def test_second_detach_idempotent(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session, spec={"cidr": "10.77.12.0/24", "gateway_ip": "10.77.12.1"})
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=22)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=22,
            container_id="c",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        adapter.detach_workspace(topology_id=tid, node_id="n1", workspace_id=22)
        second = adapter.detach_workspace(topology_id=tid, node_id="n1", workspace_id=22)
        assert second.detached is False
        assert second.status == TopologyAttachmentStatus.DETACHED


class TestDeleteTopology:
    def test_noop_when_runtime_missing(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        adapter.delete_topology(topology_id=tid, node_id="no-runtime-node")
        assert (
            topo_session.exec(
                select(TopologyRuntime).where(
                    TopologyRuntime.topology_id == tid,
                    TopologyRuntime.node_id == "no-runtime-node",
                ),
            ).first()
            is None
        )

    def test_raises_when_workspace_still_attached(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.70.0/24", "gateway_ip": "10.77.70.1", "bridge_name": "br-del1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-del")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-del", workspace_id=70)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n-del",
            workspace_id=70,
            container_id="c70",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        with pytest.raises(TopologyDeleteError, match="non-DETACHED"):
            adapter.delete_topology(topology_id=tid, node_id="n-del")

    def test_raises_when_failed_attachment_row(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.71.0/24", "gateway_ip": "10.77.71.1"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-fl")
        now = datetime.now(timezone.utc)
        topo_session.add(
            TopologyAttachment(
                topology_id=tid,
                node_id="n-fl",
                workspace_id=71,
                container_id="c71",
                status=TopologyAttachmentStatus.FAILED,
                workspace_ip="10.77.71.2",
                bridge_name="brx",
                gateway_ip="10.77.71.1",
                created_at=now,
                updated_at=now,
            ),
        )
        topo_session.commit()
        with pytest.raises(TopologyDeleteError, match="non-DETACHED"):
            adapter.delete_topology(topology_id=tid, node_id="n-fl")

    def test_deletes_runtime_and_detached_attachments(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.72.0/24", "gateway_ip": "10.77.72.1", "bridge_name": "br-del2"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-ok")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-ok", workspace_id=72)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n-ok",
            workspace_id=72,
            container_id="c72",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        adapter.detach_workspace(topology_id=tid, node_id="n-ok", workspace_id=72)
        rid = topo_session.exec(
            select(TopologyRuntime.topology_runtime_id).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == "n-ok",
            ),
        ).first()
        assert rid is not None
        adapter.delete_topology(topology_id=tid, node_id="n-ok")
        assert topo_session.get(TopologyRuntime, rid) is None
        assert (
            topo_session.exec(
                select(TopologyAttachment).where(
                    TopologyAttachment.topology_id == tid,
                    TopologyAttachment.node_id == "n-ok",
                ),
            ).first()
            is None
        )

    def test_linux_bridge_remove_when_apply_bridge(self, topo_session: Session) -> None:
        calls: list[list[str]] = []

        class FakeRunner:
            def run(self, cmd: list[str]) -> str:
                calls.append(list(cmd))
                if "show" in cmd:
                    return "1: br-rm: state UP\n"
                return ""

        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.73.0/24", "gateway_ip": "10.77.73.1", "bridge_name": "br-rm"},
        )
        DbTopologyAdapter(topo_session).ensure_node_topology(topology_id=tid, node_id="n-rm")
        d_ad = DbTopologyAdapter(
            topo_session,
            command_runner=FakeRunner(),
            apply_linux_bridge=True,
        )
        d_ad.delete_topology(topology_id=tid, node_id="n-rm")
        assert any(c[:4] == ["ip", "link", "del", "dev"] for c in calls)


class TestCheckTopology:
    def test_healthy_when_runtime_ready_and_populated(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.20.0/24", "gateway_ip": "10.77.20.1", "bridge_name": "br-chk"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-chk")
        res = adapter.check_topology(topology_id=tid, node_id="n-chk")
        assert res.healthy is True
        assert res.status == TopologyRuntimeStatus.READY
        assert res.issues == ()
        assert res.topology_runtime_id is not None
        assert res.bridge_name == "br-chk"
        assert res.cidr == "10.77.20.0/24"
        assert res.gateway_ip == "10.77.20.1"

    def test_unhealthy_when_runtime_missing(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        res = adapter.check_topology(topology_id=tid, node_id="no-runtime")
        assert res.healthy is False
        assert res.status == TopologyRuntimeStatus.FAILED
        assert res.topology_runtime_id is None
        assert any("not found" in i for i in res.issues)

    def test_unhealthy_when_runtime_incomplete(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-inc")
        row = topo_session.exec(
            select(TopologyRuntime).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == "n-inc",
            ),
        ).first()
        assert row is not None
        row.bridge_name = None
        topo_session.add(row)
        topo_session.commit()
        res = adapter.check_topology(topology_id=tid, node_id="n-inc")
        assert res.healthy is False
        assert "bridge_name" in " ".join(res.issues)

    def test_linux_topology_issues_when_bridge_absent_on_host(self, topo_session: Session) -> None:
        class FailRunner:
            def run(self, cmd: list[str]) -> str:
                raise RuntimeError("no such device")

        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.60.0/24", "gateway_ip": "10.77.60.1", "bridge_name": "br-lx"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-lx")
        chk = DbTopologyAdapter(
            topo_session,
            command_runner=FailRunner(),
            apply_linux_bridge=True,
        )
        res = chk.check_topology(topology_id=tid, node_id="n-lx")
        assert res.healthy is False
        assert any(i.startswith("linux:") and "bridge" in i for i in res.issues)

    def test_linux_topology_healthy_when_ip_reports_bridge_ok(self, topo_session: Session) -> None:
        gw, pfx = "10.77.61.1", 24

        class GoodRunner:
            def run(self, cmd: list[str]) -> str:
                if cmd[:4] == ["ip", "link", "show", "dev"]:
                    return (
                        "3: br-ok: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
                        "qdisc noqueue state UP mode DEFAULT group default\n"
                    )
                if cmd[:6] == ["ip", "-o", "-4", "addr", "show", "dev"]:
                    return f"3: br-ok    inet {gw}/{pfx} brd 10.77.61.255 scope global br-ok\n"
                raise AssertionError(f"unexpected cmd: {cmd}")

        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.61.0/24", "gateway_ip": gw, "bridge_name": "br-ok"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n-ok")
        chk = DbTopologyAdapter(
            topo_session,
            command_runner=GoodRunner(),
            apply_linux_bridge=True,
        )
        res = chk.check_topology(topology_id=tid, node_id="n-ok")
        assert res.healthy is True
        assert res.issues == ()


class TestCheckAttachment:
    def test_healthy_after_attach(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.30.0/24", "gateway_ip": "10.77.30.1", "bridge_name": "br-a"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=30)
        att = adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=30,
            container_id="cid-h",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        res = adapter.check_attachment(topology_id=tid, node_id="n1", workspace_id=30)
        assert res.healthy is True
        assert res.status == TopologyAttachmentStatus.ATTACHED
        assert res.issues == ()
        assert res.attachment_id == att.attachment_id
        assert res.internal_endpoint == f"{ip.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"

    def test_unhealthy_when_missing(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session)
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        res = adapter.check_attachment(topology_id=tid, node_id="n1", workspace_id=404)
        assert res.healthy is False
        assert res.attachment_id is None
        assert any("not found" in i for i in res.issues)

    def test_unhealthy_after_detach(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session, spec={"cidr": "10.77.32.0/24", "gateway_ip": "10.77.32.1"})
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=32)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=32,
            container_id="c",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        adapter.detach_workspace(topology_id=tid, node_id="n1", workspace_id=32)
        res = adapter.check_attachment(topology_id=tid, node_id="n1", workspace_id=32)
        assert res.healthy is False
        assert res.status == TopologyAttachmentStatus.DETACHED

    def test_unhealthy_when_ip_mismatch(self, topo_session: Session) -> None:
        tid = _insert_topology(topo_session, spec={"cidr": "10.77.31.0/24", "gateway_ip": "10.77.31.1"})
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=31)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=31,
            container_id="c",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        row = topo_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.workspace_id == 31,
            ),
        ).first()
        assert row is not None
        row.workspace_ip = "10.77.31.99"
        topo_session.add(row)
        topo_session.commit()
        res = adapter.check_attachment(topology_id=tid, node_id="n1", workspace_id=31)
        assert res.healthy is False
        assert any("lease" in i or "match" in i for i in res.issues)

    def test_linux_attachment_issues_when_host_veth_missing(self, topo_session: Session) -> None:
        class FailRunner:
            def run(self, cmd: list[str]) -> str:
                raise RuntimeError("not found")

        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.62.0/24", "gateway_ip": "10.77.62.1", "bridge_name": "br-v"},
        )
        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=62)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=62,
            container_id="c62",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        chk = DbTopologyAdapter(
            topo_session,
            command_runner=FailRunner(),
            apply_linux_attachment=True,
        )
        res = chk.check_attachment(topology_id=tid, node_id="n1", workspace_id=62)
        assert res.healthy is False
        assert any(i.startswith("linux:") and "veth" in i for i in res.issues)

    def test_linux_attachment_healthy_when_ip_shows_veth_on_bridge(self, topo_session: Session) -> None:
        tid = _insert_topology(
            topo_session,
            spec={"cidr": "10.77.63.0/24", "gateway_ip": "10.77.63.1", "bridge_name": "br-veth"},
        )
        host_if, _ = _veth_pair_names(tid, "n1", 63)

        class GoodRunner:
            def run(self, cmd: list[str]) -> str:
                if cmd[:4] == ["ip", "link", "show", "dev"]:
                    if cmd[4] == host_if:
                        return (
                            f"9: {host_if}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
                            f"qdisc noqueue master br-veth state UP mode DEFAULT group default\n"
                        )
                raise AssertionError(f"unexpected cmd: {cmd}")

        adapter = DbTopologyAdapter(topo_session)
        adapter.ensure_node_topology(topology_id=tid, node_id="n1")
        ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n1", workspace_id=63)
        adapter.attach_workspace(
            topology_id=tid,
            node_id="n1",
            workspace_id=63,
            container_id="c63",
            netns_ref="/ns",
            workspace_ip=ip.workspace_ip,
        )
        chk = DbTopologyAdapter(
            topo_session,
            command_runner=GoodRunner(),
            apply_linux_attachment=True,
        )
        res = chk.check_attachment(topology_id=tid, node_id="n1", workspace_id=63)
        assert res.healthy is True
        assert res.issues == ()
