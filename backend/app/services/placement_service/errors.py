"""Placement-layer errors (control plane; surfaced on workspace jobs when scheduling fails)."""


class PlacementError(Exception):
    """Base class for placement failures."""


class NoSchedulableNodeError(PlacementError):
    """No execution node satisfies policy (capacity, health, schedulable flag)."""


class ExecutionNodeNotFoundError(PlacementError):
    """Requested node id/key does not exist."""


class InvalidPlacementParametersError(PlacementError):
    """Invalid inputs to placement (e.g. non-positive capacity request)."""
