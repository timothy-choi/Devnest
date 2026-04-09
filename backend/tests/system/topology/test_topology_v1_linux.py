"""Real Linux + Docker checks for Topology V1 (bridge, veth, netns).

See ``README.md`` for host requirements. Does not publish workspace IDE to a fixed host port;
``internal_endpoint`` is only ``workspace_ip:WORKSPACE_IDE_CONTAINER_PORT`` (in-container).
"""

from __future__ import annotations

import subprocess
import uuid

import pytest
from sqlmodel import Session, select

from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology.models import Topology, TopologyAttachment
from app.libs.topology.models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus

pytestmark = [pytest.mark.system, pytest.mark.topology_linux]


def _unique_ipv4_subnet() -> tuple[str, str]:
    """Pick a /24 in 10.240–10.247.x (unlikely to overlap Docker/kube defaults on CI hosts)."""
    b = uuid.uuid4().bytes
    second = 240 + (b[0] % 8)
    third = b[1]
    return f"10.{second}.{third}.0/24", f"10.{second}.{third}.1"


def seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name="sys-topology", version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


def ip_link_show_dev(ifname: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["ip", "link", "show", "dev", ifname],
        capture_output=True,
        text=True,
        timeout=10,
    )
    ok = r.returncode == 0
    return ok, (r.stdout or "") + (r.stderr or "")


def _force_cleanup_node_bridge(adapter, topology_id: int, node_id: str, bridge: str) -> None:
    """Best-effort DB + host cleanup so a failed test does not leak bridges."""
    try:
        adapter.delete_topology(topology_id=topology_id, node_id=node_id)
    except Exception:
        pass
    subprocess.run(
        ["ip", "link", "del", "dev", bridge],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_ensure_node_topology_creates_bridge_on_host(
    linux_net_admin_or_skip: None,
    linux_topology_adapter,
    topology_sqlite_session: Session,
) -> None:
    cidr, gw = _unique_ipv4_subnet()
    bridge = f"b{uuid.uuid4().hex[:6]}"
    tid = seed_topology(
        topology_sqlite_session,
        spec={"cidr": cidr, "gateway_ip": gw, "bridge_name": bridge},
    )
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    try:
        out = linux_topology_adapter.ensure_node_topology(topology_id=tid, node_id=node_id)
        assert out.bridge_name == bridge
        assert out.status == TopologyRuntimeStatus.READY
        exists, _out = ip_link_show_dev(bridge)
        assert exists, f"bridge {bridge!r} missing on host after ensure_node_topology"
    finally:
        _force_cleanup_node_bridge(linux_topology_adapter, tid, node_id, bridge)


def test_allocate_workspace_ip_reuses_lease_and_unique_ips(
    linux_net_admin_or_skip: None,
    linux_topology_adapter,
    topology_sqlite_session: Session,
) -> None:
    cidr, gw = _unique_ipv4_subnet()
    bridge = f"b{uuid.uuid4().hex[:6]}"
    tid = seed_topology(
        topology_sqlite_session,
        spec={"cidr": cidr, "gateway_ip": gw, "bridge_name": bridge},
    )
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    try:
        linux_topology_adapter.ensure_node_topology(topology_id=tid, node_id=node_id)
        a1 = linux_topology_adapter.allocate_workspace_ip(
            topology_id=tid,
            node_id=node_id,
            workspace_id=101,
        )
        a2 = linux_topology_adapter.allocate_workspace_ip(
            topology_id=tid,
            node_id=node_id,
            workspace_id=101,
        )
        assert a1.workspace_ip == a2.workspace_ip
        assert a2.leased_existing is True
        b = linux_topology_adapter.allocate_workspace_ip(
            topology_id=tid,
            node_id=node_id,
            workspace_id=102,
        )
        assert b.workspace_ip != a1.workspace_ip
        assert gw not in (a1.workspace_ip, b.workspace_ip)
    finally:
        _force_cleanup_node_bridge(linux_topology_adapter, tid, node_id, bridge)


def test_check_topology_verifies_live_bridge(
    linux_net_admin_or_skip: None,
    linux_topology_adapter,
    topology_sqlite_session: Session,
) -> None:
    cidr, gw = _unique_ipv4_subnet()
    bridge = f"b{uuid.uuid4().hex[:6]}"
    tid = seed_topology(
        topology_sqlite_session,
        spec={"cidr": cidr, "gateway_ip": gw, "bridge_name": bridge},
    )
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    try:
        linux_topology_adapter.ensure_node_topology(topology_id=tid, node_id=node_id)
        chk = linux_topology_adapter.check_topology(topology_id=tid, node_id=node_id)
        assert chk.healthy is True, chk.issues
        assert chk.bridge_name == bridge
        assert not any(i.startswith("linux:") for i in chk.issues)

        # Simulate drift: remove bridge on host, DB still READY
        subprocess_rm = subprocess.run(
            ["ip", "link", "del", "dev", bridge],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert subprocess_rm.returncode == 0, subprocess_rm.stderr
        chk2 = linux_topology_adapter.check_topology(topology_id=tid, node_id=node_id)
        assert chk2.healthy is False
        assert any(i.startswith("linux:") for i in chk2.issues)
    finally:
        _force_cleanup_node_bridge(linux_topology_adapter, tid, node_id, bridge)


def test_attach_detach_check_attachment_round_trip(
    linux_net_admin_or_skip: None,
    linux_topology_adapter,
    topology_sqlite_session: Session,
    alpine_netns_container: tuple[str, int],
) -> None:
    cidr, gw = _unique_ipv4_subnet()
    bridge = f"b{uuid.uuid4().hex[:6]}"
    tid = seed_topology(
        topology_sqlite_session,
        spec={"cidr": cidr, "gateway_ip": gw, "bridge_name": bridge},
    )
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    ws_id = 9001 + (uuid.uuid4().int % 1000)

    try:
        linux_topology_adapter.ensure_node_topology(topology_id=tid, node_id=node_id)
        ip_res = linux_topology_adapter.allocate_workspace_ip(
            topology_id=tid,
            node_id=node_id,
            workspace_id=ws_id,
        )
        _cname, pid = alpine_netns_container
        netns_ref = f"/proc/{pid}/ns/net"

        att = linux_topology_adapter.attach_workspace(
            topology_id=tid,
            node_id=node_id,
            workspace_id=ws_id,
            container_id=f"cid-{uuid.uuid4().hex[:8]}",
            netns_ref=netns_ref,
            workspace_ip=ip_res.workspace_ip,
        )
        expected_ep = f"{ip_res.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        assert att.internal_endpoint == expected_ep
        assert expected_ep.endswith(f":{WORKSPACE_IDE_CONTAINER_PORT}")
        # internal endpoint must not assume a fixed host-published port (no host port in string)
        assert ":" in expected_ep and expected_ep.count(":") == 1

        row = topology_sqlite_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.workspace_id == ws_id,
            ),
        ).first()
        assert row is not None and row.interface_host
        host_if = str(row.interface_host).strip()
        ok_pre, _ = ip_link_show_dev(host_if)
        assert ok_pre, "host veth leg should exist on host after attach"

        chk_att = linux_topology_adapter.check_attachment(
            topology_id=tid,
            node_id=node_id,
            workspace_id=ws_id,
        )
        assert chk_att.healthy is True, chk_att.issues
        assert chk_att.internal_endpoint == expected_ep
        assert not any(i.startswith("linux:") for i in chk_att.issues)

        linux_topology_adapter.detach_workspace(topology_id=tid, node_id=node_id, workspace_id=ws_id)
        ok_post, _ = ip_link_show_dev(host_if)
        assert not ok_post, "host veth should be gone after detach_workspace"

        chk_det = linux_topology_adapter.check_attachment(
            topology_id=tid,
            node_id=node_id,
            workspace_id=ws_id,
        )
        assert chk_det.healthy is False
        assert chk_det.status == TopologyAttachmentStatus.DETACHED

        linux_topology_adapter.delete_topology(topology_id=tid, node_id=node_id)
        ok_br, _ = ip_link_show_dev(bridge)
        assert not ok_br, "bridge should be removed after delete_topology"
    except Exception:
        try:
            linux_topology_adapter.detach_workspace(
                topology_id=tid,
                node_id=node_id,
                workspace_id=ws_id,
            )
        except Exception:
            pass
        _force_cleanup_node_bridge(linux_topology_adapter, tid, node_id, bridge)
        raise


def test_delete_topology_blocked_while_attached(
    linux_net_admin_or_skip: None,
    linux_topology_adapter,
    topology_sqlite_session: Session,
    alpine_netns_container: tuple[str, int],
) -> None:
    from app.libs.topology.errors import TopologyDeleteError

    cidr, gw = _unique_ipv4_subnet()
    bridge = f"b{uuid.uuid4().hex[:6]}"
    tid = seed_topology(
        topology_sqlite_session,
        spec={"cidr": cidr, "gateway_ip": gw, "bridge_name": bridge},
    )
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    ws_id = 8000 + (uuid.uuid4().int % 500)
    linux_topology_adapter.ensure_node_topology(topology_id=tid, node_id=node_id)
    ip_res = linux_topology_adapter.allocate_workspace_ip(
        topology_id=tid,
        node_id=node_id,
        workspace_id=ws_id,
    )
    _cname, pid = alpine_netns_container
    linux_topology_adapter.attach_workspace(
        topology_id=tid,
        node_id=node_id,
        workspace_id=ws_id,
        container_id="live",
        netns_ref=f"/proc/{pid}/ns/net",
        workspace_ip=ip_res.workspace_ip,
    )
    try:
        with pytest.raises(TopologyDeleteError, match="non-DETACHED"):
            linux_topology_adapter.delete_topology(topology_id=tid, node_id=node_id)
    finally:
        try:
            linux_topology_adapter.detach_workspace(topology_id=tid, node_id=node_id, workspace_id=ws_id)
        except Exception:
            pass
        _force_cleanup_node_bridge(linux_topology_adapter, tid, node_id, bridge)
