"""Quota enforcement errors."""

from __future__ import annotations


class QuotaExceededError(Exception):
    """Raised when a quota limit is reached for a platform operation.

    Maps to HTTP 429 Too Many Requests at the API layer.
    """

    def __init__(
        self,
        *,
        quota_field: str,
        limit: int | float,
        current: int | float,
        scope: str = "unknown",
    ) -> None:
        self.quota_field = quota_field
        self.limit = limit
        self.current = current
        self.scope = scope
        super().__init__(
            f"Quota exceeded: {quota_field} (limit={limit}, current={current}, scope={scope})"
        )
