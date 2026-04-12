"""Autoscaler decision records (serializable to internal API JSON)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScaleUpEvaluation:
    should_provision: bool
    reason: str
    provisioning_in_flight: int


@dataclass(frozen=True)
class ScaleDownEvaluation:
    """Candidate for reclaim; ``node_key`` empty when none."""

    node_key: str | None
    reason: str
    idle_ec2_ready_nodes: int
