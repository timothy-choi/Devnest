"""Policy service enumerations."""

from enum import Enum


class PolicyType(str, Enum):
    """Classifies who owns / manages the policy."""

    SYSTEM = "system"
    USER = "user"
    WORKSPACE = "workspace"
    # TODO: ORG = "org" when multi-tenancy is introduced


class ScopeType(str, Enum):
    """Determines which entities a policy or quota applies to."""

    GLOBAL = "global"
    USER = "user"
    WORKSPACE = "workspace"
