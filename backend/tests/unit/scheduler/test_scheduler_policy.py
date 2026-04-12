"""Unit tests: pure scheduler policy."""

from __future__ import annotations

from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.scheduler_service.models import WorkspaceComputeRequest
from app.services.scheduler_service.policy import can_fit_workspace, rank_candidate_nodes, scheduling_sort_key


def _node(
    *,
    key: str,
    alloc_cpu: float,
    alloc_mem: int,
) -> ExecutionNode:
    return ExecutionNode(
        node_key=key,
        name=key,
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=max(alloc_cpu, 0.25),
        total_memory_mb=max(alloc_mem, 256),
        allocatable_cpu=alloc_cpu,
        allocatable_memory_mb=alloc_mem,
    )


def test_can_fit_workspace_requires_cpu_and_memory() -> None:
    req = WorkspaceComputeRequest(requested_cpu=1.0, requested_memory_mb=512)
    assert can_fit_workspace(_node(key="a", alloc_cpu=2.0, alloc_mem=1024), req) is True
    assert can_fit_workspace(_node(key="b", alloc_cpu=0.5, alloc_mem=1024), req) is False
    assert can_fit_workspace(_node(key="c", alloc_cpu=2.0, alloc_mem=256), req) is False


def test_rank_candidate_nodes_best_fit_then_lexicographic() -> None:
    req = WorkspaceComputeRequest(requested_cpu=0.5, requested_memory_mb=256)
    n_big = _node(key="z", alloc_cpu=4.0, alloc_mem=8192)
    n_small = _node(key="a", alloc_cpu=4.0, alloc_mem=4096)
    n_tiny_cpu = _node(key="m", alloc_cpu=8.0, alloc_mem=512)
    out = rank_candidate_nodes([n_small, n_big, n_tiny_cpu], req)
    # Highest alloc_cpu first: n_tiny_cpu (8.0), then tie-break RAM: n_big, n_small
    assert out[0].node_key == "m"
    assert out[1].node_key == "z"
    assert out[2].node_key == "a"


def test_scheduling_sort_key_matches_rank_order() -> None:
    req = WorkspaceComputeRequest(requested_cpu=0.1, requested_memory_mb=128)
    nodes = [_node(key="b", alloc_cpu=2.0, alloc_mem=1000), _node(key="a", alloc_cpu=2.0, alloc_mem=1000)]
    ranked = rank_candidate_nodes(nodes, req)
    assert sorted(ranked, key=scheduling_sort_key) == ranked
