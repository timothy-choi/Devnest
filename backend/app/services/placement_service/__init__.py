"""Execution node registry and V1 placement selection."""

from .bootstrap import ensure_default_local_execution_node
from .capacity import count_active_workloads_on_node_key, total_reserved_on_node_key
from .errors import (
    AuthoritativePlacementError,
    ExecutionNodeNotFoundError,
    InvalidPlacementParametersError,
    NoSchedulableNodeError,
    PlacementError,
)
from .models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from .node_placement import (
    get_node,
    list_schedulable_nodes,
    reserve_node_for_workspace,
    schedulable_placement_predicates,
    select_node_for_workspace,
    touch_node_heartbeat,
)
from .orchestrator_binding import resolve_orchestrator_placement

__all__ = [
    "AuthoritativePlacementError",
    "ExecutionNode",
    "ExecutionNodeExecutionMode",
    "ExecutionNodeNotFoundError",
    "ExecutionNodeProviderType",
    "ExecutionNodeStatus",
    "InvalidPlacementParametersError",
    "NoSchedulableNodeError",
    "PlacementError",
    "count_active_workloads_on_node_key",
    "ensure_default_local_execution_node",
    "get_node",
    "list_schedulable_nodes",
    "reserve_node_for_workspace",
    "resolve_orchestrator_placement",
    "schedulable_placement_predicates",
    "select_node_for_workspace",
    "total_reserved_on_node_key",
    "touch_node_heartbeat",
]
