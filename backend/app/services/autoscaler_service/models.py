"""Autoscaler decision records (serializable to internal API JSON)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScaleUpEvaluation:
    should_provision: bool
    reason: str
    provisioning_in_flight: int
    # Number of READY+schedulable EC2 nodes with zero active workloads at evaluation time.
    # Non-zero means scale-up was or should be suppressed (prefer reuse over provisioning).
    idle_ec2_node_count: int = field(default=0)


@dataclass(frozen=True)
class ScaleDownEvaluation:
    """Candidate for reclaim; ``node_key`` empty when none."""

    node_key: str | None
    reason: str
    idle_ec2_ready_nodes: int
