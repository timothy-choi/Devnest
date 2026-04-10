"""Integration tests: concurrent topology allocation on PostgreSQL (real DB constraints + retries).

Uses one ``Session`` per thread (sessions are not thread-safe). Linux bridge/attachment ops are
skipped via ``integration/topology/conftest.py`` autouse env (same as other topology DB tests).
"""

from __future__ import annotations

import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import IpAllocation, Topology, TopologyRuntime

pytestmark = [
    pytest.mark.concurrency,
    pytest.mark.slow,
    pytest.mark.topology_heavy,
]

# Cap worker count so we do not exhaust the SQLAlchemy pool (default pool is small on many setups).
_MAX_POOL_WORKERS = 8


def _seed_topology(session: Session, *, name: str, spec: dict | None = None) -> int:
    t = Topology(name=name, version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


def test_concurrent_ensure_node_topology_auto_pool_unique_non_overlapping_cidrs(
    test_engine: Engine,
) -> None:
    """Many nodes racing ``ensure_node_topology`` each receive a distinct runtime CIDR (pool path)."""
    tid: int
    with Session(test_engine) as s:
        tid = _seed_topology(s, name="conc-pool-topo", spec={})

    node_ids = [f"pool-conc-{i}" for i in range(10)]
    barrier = threading.Barrier(len(node_ids))

    def ensure_one(nid: str) -> None:
        barrier.wait()
        with Session(test_engine) as s2:
            DbTopologyAdapter(s2).ensure_node_topology(topology_id=tid, node_id=nid)

    workers = min(_MAX_POOL_WORKERS, len(node_ids))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(ensure_one, nid) for nid in node_ids]
        for f in as_completed(futs):
            f.result()  # propagate worker exceptions; barrier prevents partial completion hangs

    with Session(test_engine) as s:
        rows = s.exec(
            select(TopologyRuntime).where(TopologyRuntime.topology_id == tid),
        ).all()
        assert len(rows) == len(node_ids)
        cidrs = [r.cidr for r in rows]
        assert all(c and str(c).strip() for c in cidrs)
        assert len(set(cidrs)) == len(cidrs), "duplicate CIDR strings assigned to different nodes"

        nets = [ipaddress.ip_network(str(c).strip(), strict=False) for c in cidrs]
        for i, a in enumerate(nets):
            for b in nets[i + 1 :]:
                assert not a.overlaps(b), f"overlapping runtime subnets: {a} vs {b}"


def test_concurrent_ensure_node_topology_same_node_idempotent(
    test_engine: Engine,
) -> None:
    """Racing ``ensure_node_topology`` for one node leaves a single row and a stable CIDR."""
    tid: int
    with Session(test_engine) as s:
        tid = _seed_topology(
            s,
            name="conc-same-node",
            spec={"cidr": "10.88.50.0/24", "gateway_ip": "10.88.50.1"},
        )

    nid = "same-node-race"
    n_calls = 12
    barrier = threading.Barrier(n_calls)

    def ensure_once(_: int) -> str:
        barrier.wait()
        with Session(test_engine) as s2:
            out = DbTopologyAdapter(s2).ensure_node_topology(topology_id=tid, node_id=nid)
            assert out.cidr == "10.88.50.0/24"
            return str(out.cidr)

    workers = min(_MAX_POOL_WORKERS, n_calls)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        cidrs = list(pool.map(ensure_once, range(n_calls)))

    assert len(set(cidrs)) == 1

    with Session(test_engine) as s:
        rows = s.exec(
            select(TopologyRuntime).where(
                TopologyRuntime.topology_id == tid,
                TopologyRuntime.node_id == nid,
            ),
        ).all()
        assert len(rows) == 1
        assert rows[0].cidr == "10.88.50.0/24"


class _AllocOutcome(NamedTuple):
    workspace_id: int
    workspace_ip: str
    leased_existing: bool


def test_concurrent_allocate_workspace_ip_distinct_active_ips(
    test_engine: Engine,
) -> None:
    """Concurrent first-time allocations for different workspaces yield unique leased IPs (PG partial unique index)."""
    tid: int
    with Session(test_engine) as s:
        tid = _seed_topology(
            s,
            name="conc-ip-topo",
            spec={"cidr": "10.88.60.0/24", "gateway_ip": "10.88.60.1"},
        )
        DbTopologyAdapter(s).ensure_node_topology(topology_id=tid, node_id="n-ip-race")

    node_id = "n-ip-race"
    workspace_ids = list(range(3_000, 3_000 + 24))
    barrier = threading.Barrier(len(workspace_ids))

    def allocate(wid: int) -> _AllocOutcome:
        barrier.wait()
        with Session(test_engine) as s2:
            res = DbTopologyAdapter(s2).allocate_workspace_ip(
                topology_id=tid,
                node_id=node_id,
                workspace_id=wid,
            )
            return _AllocOutcome(wid, res.workspace_ip, res.leased_existing)

    workers = min(_MAX_POOL_WORKERS, len(workspace_ids))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(allocate, workspace_ids))

    assert [o.workspace_id for o in outcomes] == workspace_ids
    ips = [o.workspace_ip for o in outcomes]
    assert len(ips) == len(workspace_ids)
    assert len(set(ips)) == len(workspace_ids), f"duplicate workspace_ip in outcomes: {ips}"
    assert not any(o.leased_existing for o in outcomes), "first-time allocates should not all be lease reuse"

    with Session(test_engine) as s:
        rows = s.exec(
            select(IpAllocation).where(
                IpAllocation.topology_id == tid,
                IpAllocation.node_id == node_id,
                IpAllocation.released_at.is_(None),  # type: ignore[union-attr]
            ),
        ).all()
        assert len(rows) == len(workspace_ids)
        active_ips = {r.ip for r in rows}
        assert len(active_ips) == len(rows), "duplicate active IP rows in ip_allocation"


def test_concurrent_allocate_workspace_ip_same_workspace_converges_single_ip(
    test_engine: Engine,
) -> None:
    """Concurrent calls for the same ``workspace_id`` reuse one lease (no duplicate active IPs)."""
    tid: int
    with Session(test_engine) as s:
        tid = _seed_topology(
            s,
            name="conc-same-ws",
            spec={"cidr": "10.88.61.0/24", "gateway_ip": "10.88.61.1"},
        )
        DbTopologyAdapter(s).ensure_node_topology(topology_id=tid, node_id="n-same-ws")

    node_id = "n-same-ws"
    wid = 42_424
    n_calls = 16
    barrier = threading.Barrier(n_calls)

    def allocate_same(_: int) -> _AllocOutcome:
        barrier.wait()
        with Session(test_engine) as s2:
            res = DbTopologyAdapter(s2).allocate_workspace_ip(
                topology_id=tid,
                node_id=node_id,
                workspace_id=wid,
            )
            return _AllocOutcome(wid, res.workspace_ip, res.leased_existing)

    workers = min(_MAX_POOL_WORKERS, n_calls)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(allocate_same, range(n_calls)))

    assert len({o.workspace_ip for o in outcomes}) == 1
    assert any(o.leased_existing for o in outcomes), "expected at least one reused lease after first commit"

    with Session(test_engine) as s:
        rows = s.exec(
            select(IpAllocation).where(
                IpAllocation.topology_id == tid,
                IpAllocation.node_id == node_id,
                IpAllocation.workspace_id == wid,
            ),
        ).all()
        assert len(rows) == 1
        assert rows[0].released_at is None
