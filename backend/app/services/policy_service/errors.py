"""Policy enforcement errors."""

from __future__ import annotations


class PolicyViolationError(Exception):
    """Raised when an active policy blocks a platform operation.

    Maps to HTTP 403 Forbidden at the API layer.
    """

    def __init__(self, *, policy_name: str, action: str, reason: str) -> None:
        self.policy_name = policy_name
        self.action = action
        self.reason = reason
        super().__init__(f"Policy violation [{policy_name}] blocked '{action}': {reason}")
