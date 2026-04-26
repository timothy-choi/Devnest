"""Operator-only pinned CREATE placement (Phase 3b Step 8).

When enabled, CREATE jobs for workspaces that match :func:`workspace_uses_operator_pinned_create`
skip the scheduler and use the pre-set ``Workspace.execution_node_id`` instead.
"""

from __future__ import annotations

from app.libs.common.config import Settings, get_settings
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
