"""Integration tests: ``delete_topology`` safety and idempotency on PostgreSQL.

Linux bridge calls are exercised with injected ``CommandRunner`` fakes; real ``ip`` behavior lives in
``tests/system/topology/``. Per-node isolation uses the same worker DB as other topology integration tests.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.libs.topology import DbTopologyAdapter
from app.libs.topology.errors import TopologyDeleteError
from app.libs.topology.models import IpAllocation, Topology, TopologyAttachment, TopologyRuntime
from app.libs.topology.models.enums import TopologyAttachmentStatus

pytestmark = [
    pytest.mark.slow,
    pytest.mark.topology_heavy,
    pytest.mark.failure_path,
]


def _seed_topology(session: Session, *, name: str, spec: dict | None = None) -> int:
    t = Topology(name=name, version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


class _RecordingBridgeRunner:
    """Minimal fake for ``remove_bridge_if_exists`` / ``check_bridge_exists``."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, cmd: list[str]) -> str:
        argv = list(cmd)
        self.commands.append(argv)
        if "show" in argv:
            return "1: br-int-del: state UP\n"
        return ""


class _BridgeDelFailsRunner:
    """Bridge appears to exist; ``ip link del`` raises (post-DB-commit path)."""

    def run(self, cmd: list[str]) -> str:
        argv = list(cmd)
        if len(argv) >= 4 and argv[0] == "ip" and argv[1] == "link" and argv[2] == "del":
            raise RuntimeError("simulated bridge del failure")
        if "show" in argv:
            return "1: br-fail-del: state UP\n"
        return ""


def test_delete_topology_blocked_when_workspace_attached(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        name="del-block-att",
        spec={"cidr": "10.88.80.0/24", "gateway_ip": "10.88.80.1", "bridge_name": "br-dblk"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-block")
    ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-block", workspace_id=801)
    adapter.attach_workspace(
        topology_id=tid,
        node_id="n-block",
        workspace_id=801,
        container_id="c801",
        netns_ref="/proc/1/ns/net",
        workspace_ip=ip.workspace_ip,
    )
    rt_before = db_session.exec(
        select(TopologyRuntime).where(
            TopologyRuntime.topology_id == tid,
            TopologyRuntime.node_id == "n-block",
        ),
    ).first()
    att_before = db_session.exec(
        select(TopologyAttachment).where(
            TopologyAttachment.topology_id == tid,
            TopologyAttachment.node_id == "n-block",
            TopologyAttachment.workspace_id == 801,
        ),
    ).first()
    assert rt_before is not None
    assert att_before is not None
    assert att_before.status == TopologyAttachmentStatus.ATTACHED

    with pytest.raises(TopologyDeleteError, match="non-DETACHED"):
        adapter.delete_topology(topology_id=tid, node_id="n-block")

    db_session.expire_all()

    rt_after = db_session.exec(
        select(TopologyRuntime).where(
            TopologyRuntime.topology_id == tid,
            TopologyRuntime.node_id == "n-block",
        ),
    ).first()
    att_after = db_session.get(TopologyAttachment, att_before.attachment_id)
    lease = db_session.exec(
        select(IpAllocation).where(
            IpAllocation.topology_id == tid,
            IpAllocation.workspace_id == 801,
        ),
    ).first()
    assert rt_after is not None
    assert rt_after.topology_runtime_id == rt_before.topology_runtime_id
    assert att_after is not None
    assert att_after.status == TopologyAttachmentStatus.ATTACHED
    assert att_after.container_id == "c801"
    assert lease is not None
    assert lease.released_at is None
    assert lease.ip == ip.workspace_ip


def test_delete_topology_succeeds_for_runtime_with_no_attachments(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        name="del-unused",
        spec={"cidr": "10.88.81.0/24", "gateway_ip": "10.88.81.1"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-unused")
    adapter.delete_topology(topology_id=tid, node_id="n-unused")

    assert (
        db_session.exec(
            select(TopologyRuntime).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == "n-unused",
            ),
        ).first()
        is None
    )
    topo = db_session.get(Topology, tid)
    assert topo is not None


def test_delete_topology_idempotent_three_calls_after_detach(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        name="del-idem",
        spec={"cidr": "10.88.82.0/24", "gateway_ip": "10.88.82.1"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-idem")
    ip = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-idem", workspace_id=802)
    adapter.attach_workspace(
        topology_id=tid,
        node_id="n-idem",
        workspace_id=802,
        container_id="c802",
        netns_ref="/proc/1/ns/net",
        workspace_ip=ip.workspace_ip,
    )
    adapter.detach_workspace(topology_id=tid, node_id="n-idem", workspace_id=802)

    for _ in range(3):
        adapter.delete_topology(topology_id=tid, node_id="n-idem")

    assert (
        db_session.exec(
            select(TopologyRuntime).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == "n-idem",
            ),
        ).first()
        is None
    )
    assert (
        db_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.node_id == "n-idem",
            ),
        ).first()
        is None
    )


def test_delete_topology_mocked_linux_records_bridge_removal(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        name="del-linux-mock",
        spec={"cidr": "10.88.83.0/24", "gateway_ip": "10.88.83.1", "bridge_name": "br-int-del"},
    )
    DbTopologyAdapter(db_session).ensure_node_topology(topology_id=tid, node_id="n-br")

    runner = _RecordingBridgeRunner()
    d_ad = DbTopologyAdapter(
        db_session,
        command_runner=runner,
        apply_linux_bridge=True,
    )
    d_ad.delete_topology(topology_id=tid, node_id="n-br")

    assert any(
        len(c) >= 5 and c[0] == "ip" and c[1] == "link" and c[2] == "del" and c[4] == "br-int-del"
        for c in runner.commands
    ), f"expected bridge del in {runner.commands}"


def test_delete_topology_linux_failure_after_db_commit_runtime_stays_deleted(db_session: Session) -> None:
    """DB deletion commits before bridge removal; Linux failure surfaces as error but runtime row is gone."""
    tid = _seed_topology(
        db_session,
        name="del-linux-post",
        spec={"cidr": "10.88.84.0/24", "gateway_ip": "10.88.84.1", "bridge_name": "br-fail-del"},
    )
    DbTopologyAdapter(db_session).ensure_node_topology(topology_id=tid, node_id="n-post")

    d_ad = DbTopologyAdapter(
        db_session,
        command_runner=_BridgeDelFailsRunner(),
        apply_linux_bridge=True,
    )
    with pytest.raises(TopologyDeleteError, match="linux bridge removal failed"):
        d_ad.delete_topology(topology_id=tid, node_id="n-post")

    assert (
        db_session.exec(
            select(TopologyRuntime).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == "n-post",
            ),
        ).first()
        is None
    )


def test_delete_topology_preserves_ip_leases_and_does_not_touch_other_node(db_session: Session) -> None:
    tid = _seed_topology(
        db_session,
        name="del-cross",
        spec={"cidr": "10.88.85.0/24", "gateway_ip": "10.88.85.1"},
    )
    adapter = DbTopologyAdapter(db_session)
    adapter.ensure_node_topology(topology_id=tid, node_id="n-a")
    adapter.ensure_node_topology(topology_id=tid, node_id="n-b")
    ip_a = adapter.allocate_workspace_ip(topology_id=tid, node_id="n-a", workspace_id=851)
    adapter.attach_workspace(
        topology_id=tid,
        node_id="n-a",
        workspace_id=851,
        container_id="c-a",
        netns_ref="/proc/1/ns/net",
        workspace_ip=ip_a.workspace_ip,
    )
    adapter.detach_workspace(topology_id=tid, node_id="n-a", workspace_id=851)

    adapter.delete_topology(topology_id=tid, node_id="n-a")

    rt_b = db_session.exec(
        select(TopologyRuntime).where(
            TopologyRuntime.topology_id == tid,
            TopologyRuntime.node_id == "n-b",
        ),
    ).first()
    assert rt_b is not None

    lease_a = db_session.exec(
        select(IpAllocation).where(
            IpAllocation.topology_id == tid,
            IpAllocation.node_id == "n-a",
            IpAllocation.workspace_id == 851,
        ),
    ).first()
    assert lease_a is not None
    assert lease_a.released_at is None
    assert lease_a.ip == ip_a.workspace_ip
