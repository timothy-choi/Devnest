"""Scoped internal API credentials (V1).

Each internal router uses an :class:`InternalApiScope`. Per-scope env vars override the legacy
``INTERNAL_API_KEY`` for that surface only. When a scope-specific key is set, the legacy key is
**not** accepted for that scope (credential separation). When unset, the legacy key applies.

Future: replace header static keys with mTLS or workload identity without changing route grouping.
"""

from __future__ import annotations

import secrets
from enum import Enum

from app.libs.common.config import Settings


class InternalApiScope(str, Enum):
    """Capability surface for internal HTTP routes (one credential bucket per value)."""

    WORKSPACE_JOBS = "workspace_jobs"
    WORKSPACE_RECONCILE = "workspace_reconcile"
    AUTOSCALER = "autoscaler"
    INFRASTRUCTURE = "infrastructure"
    NOTIFICATIONS = "notifications"


_SCOPE_TO_SETTINGS_FIELD: dict[InternalApiScope, str] = {
    InternalApiScope.WORKSPACE_JOBS: "internal_api_key_workspace_jobs",
    InternalApiScope.WORKSPACE_RECONCILE: "internal_api_key_workspace_reconcile",
    InternalApiScope.AUTOSCALER: "internal_api_key_autoscaler",
    InternalApiScope.INFRASTRUCTURE: "internal_api_key_infrastructure",
    InternalApiScope.NOTIFICATIONS: "internal_api_key_notifications",
}


def internal_api_expected_secrets(settings: Settings, scope: InternalApiScope) -> tuple[str, ...]:
    """Return accepted secret string(s) for ``scope`` (empty if unconfigured)."""
    field = _SCOPE_TO_SETTINGS_FIELD[scope]
    scoped = str(getattr(settings, field, "") or "").strip()
    root = str(settings.internal_api_key or "").strip()
    if scoped:
        return (scoped,)
    if root:
        return (root,)
    return ()


def internal_api_key_is_valid(provided: str | None, settings: Settings, scope: InternalApiScope) -> bool:
    """Constant-time check against configured secret(s) for ``scope``."""
    if provided is None:
        return False
    candidates = internal_api_expected_secrets(settings, scope)
    if not candidates:
        return False
    provided_s = provided.strip()
    if not provided_s:
        return False
    for expected in candidates:
        if not expected:
            continue
        if secrets.compare_digest(provided_s.encode("utf-8"), expected.encode("utf-8")):
            return True
    return False
