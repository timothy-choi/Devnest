"""Shared rules for authoritative runtime placement (production vs local dev).

Callers use these helpers so orchestrator, placement, and workers agree on when env-based
fallbacks are allowed.
"""

from __future__ import annotations

from app.libs.common.config import get_settings
from app.services.workspace_service.models import WorkspaceRuntime


def runtime_placement_row_complete(rt: WorkspaceRuntime | None) -> bool:
    """True when persisted placement is sufficient to target the correct node and topology."""
    if rt is None:
        return False
    nk = (rt.node_id or "").strip()
    return bool(nk and rt.topology_id is not None)


def runtime_env_fallback_allowed() -> bool:
    """Legacy DEVNEST_NODE_ID / DEVNEST_TOPOLOGY_ID placement; development only."""
    s = get_settings()
    return s.devnest_env == "development" and bool(s.devnest_allow_runtime_env_fallback)


def placement_strict_enforced() -> bool:
    """When True, never use env fallback and require complete WorkspaceRuntime for operational jobs."""
    return not runtime_env_fallback_allowed()


def authoritative_container_ref_required() -> bool:
    """Engine container id required for stop/delete (no deterministic name fallback)."""
    return placement_strict_enforced()
