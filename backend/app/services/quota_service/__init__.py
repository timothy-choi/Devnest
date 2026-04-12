"""Quota enforcement service for DevNest V1.

Enforces numeric resource limits (workspace count, running workspace count,
session count, snapshot count) before mutating operations.  Limits are
configured via ``Quota`` rows stored per-scope; the most specific scope wins.

TODO: add max_runtime_hours enforcement via DailyUsageAggregate rollups.
TODO: add max_cpu / max_memory_mb placement-level enforcement.
TODO: integrate with billing tiers / plan entitlements.
"""

from .errors import QuotaExceededError
from .models import Quota
from .service import (
    check_running_workspace_quota,
    check_session_quota,
    check_snapshot_quota,
    check_workspace_quota,
)

__all__ = [
    "Quota",
    "QuotaExceededError",
    "check_running_workspace_quota",
    "check_session_quota",
    "check_snapshot_quota",
    "check_workspace_quota",
]
