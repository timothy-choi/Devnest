"""Quota enforcement service for DevNest V1.

Enforces numeric resource limits (workspace count, running workspace count,
session count, snapshot count, monthly runtime hours, CPU/memory reservations)
before mutating operations. Limits are configured via ``Quota`` rows stored
per-scope; the most specific scope wins.
"""

from .errors import QuotaExceededError
from .models import Quota
from .service import (
    check_monthly_runtime_hours_quota,
    check_owner_compute_quota,
    check_running_workspace_quota,
    check_session_quota,
    check_snapshot_quota,
    check_workspace_quota,
)

__all__ = [
    "Quota",
    "QuotaExceededError",
    "check_monthly_runtime_hours_quota",
    "check_owner_compute_quota",
    "check_running_workspace_quota",
    "check_session_quota",
    "check_snapshot_quota",
    "check_workspace_quota",
]
