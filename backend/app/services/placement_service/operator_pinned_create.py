"""Operator-only pinned CREATE placement (Phase 3b Step 8).

When enabled, CREATE jobs for workspaces that match :func:`workspace_uses_operator_pinned_create`
skip the scheduler and use the pre-set ``Workspace.execution_node_id`` instead.
"""

from __future__ import annotations

from app.libs.common.config import Settings, get_settings
from app.services.placement_service.errors import InvalidPlacementParametersError
from app.services.placement_service.models import ExecutionNode
from app.services.placement_service.node_heartbeat import execution_node_heartbeat_within_max_age
from app.services.workspace_service.models import Workspace

PINNED_OPERATOR_TEST_WORKSPACE_NAME_PREFIX = "devnest-op-pinned-test-"


def parse_pinned_create_execution_node_ids(settings: Settings | None = None) -> frozenset[int]:
    s = settings or get_settings()
    raw = (s.devnest_pinned_create_execution_node_ids or "").strip()
    out: list[int] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(int(p, 10))
        except ValueError:
            continue
    return frozenset(out)


def workspace_uses_operator_pinned_create(ws: Workspace, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if not bool(s.devnest_allow_pinned_create_placement):
        return False
    if ws.execution_node_id is None:
        return False
    if int(ws.execution_node_id) not in parse_pinned_create_execution_node_ids(s):
        return False
    name = (ws.name or "").strip()
    if not name.startswith(PINNED_OPERATOR_TEST_WORKSPACE_NAME_PREFIX):
        return False
    return True


def validate_operator_pinned_create_node_gates(settings: Settings, node: ExecutionNode) -> None:
    """Phase 3b Step 8: pinned CREATE only when multi-node scheduling is on and target heartbeat is fresh.

    Normal ``POST /workspaces`` is unchanged. This gates the **internal** pinned operator path so a
    one-off node-2 test cannot run while the fleet is still on primary-only scheduling or the node
    has no recent heartbeat.

    Raises :class:`~app.services.placement_service.errors.InvalidPlacementParametersError` so worker
    ``resolve_orchestrator_placement`` failures are handled like other placement errors.
    """
    if not bool(settings.devnest_enable_multi_node_scheduling):
        raise InvalidPlacementParametersError(
            "Pinned operator CREATE requires DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true "
            "(Phase 3b Steps 7–8). Single-node mode must not admit pinned exceptions without explicit "
            "multi-node enablement.",
        )
    ok, detail = execution_node_heartbeat_within_max_age(node, settings=settings)
    if not ok:
        raise InvalidPlacementParametersError(
            f"Pinned operator CREATE requires a fresh execution node heartbeat: {detail}. "
            "POST /internal/execution-nodes/heartbeat from the target node or adjust "
            "DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS.",
        )
