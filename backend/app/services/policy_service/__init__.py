"""Policy enforcement service for DevNest V1.

Evaluates active platform policies before mutating operations.  Policies are
stored as structured JSON rules and evaluated in order of creation.  The first
matching denial wins; absence of any policy is permissive.

TODO: add support for ABAC / CEL-expression rules when a policy DSL is needed.
TODO: add org-scoped policies when multi-tenancy is introduced.
TODO: consider caching active policies per request (LRU, short TTL) for hot paths.
"""

from .errors import PolicyViolationError
from .models import Policy
from .service import (
    evaluate_node_provisioning,
    evaluate_session_creation,
    evaluate_snapshot_creation,
    evaluate_workspace_creation,
    evaluate_workspace_start,
)

__all__ = [
    "Policy",
    "PolicyViolationError",
    "evaluate_node_provisioning",
    "evaluate_session_creation",
    "evaluate_snapshot_creation",
    "evaluate_workspace_creation",
    "evaluate_workspace_start",
]
