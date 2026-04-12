"""V1 workspace scheduling: rank execution nodes and explain placement decisions.

Scheduling policy is intentionally small; :mod:`app.services.placement_service` owns DB selection
(``FOR UPDATE``) and node registry. This package centralizes **policy** and **explain** helpers.
"""

from .models import WorkspaceComputeRequest, WorkspaceScheduleResult
from .policy import can_fit_workspace, rank_candidate_nodes, scheduling_sort_key
from .service import explain_placement_decision, schedule_workspace

__all__ = [
    "WorkspaceComputeRequest",
    "WorkspaceScheduleResult",
    "can_fit_workspace",
    "explain_placement_decision",
    "rank_candidate_nodes",
    "schedule_workspace",
    "scheduling_sort_key",
]
