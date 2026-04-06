"""Runtime adapter exceptions (orchestrator may map these to workspace/job errors)."""


class RuntimeAdapterError(Exception):
    """Base class for container runtime failures surfaced by a ``RuntimeAdapter`` implementation."""


class ContainerNotFoundError(RuntimeAdapterError):
    """Raised when a referenced container id or name does not exist (e.g. start/stop/delete)."""


class ContainerCreateError(RuntimeAdapterError):
    """Raised when ``ensure_container`` cannot create or configure a container (pull, limits, invalid spec)."""


class ContainerStartError(RuntimeAdapterError):
    """Raised when ``start_container`` or the start phase of ``restart_container`` fails."""


class ContainerStopError(RuntimeAdapterError):
    """Raised when ``stop_container`` or the stop phase of ``restart_container`` fails."""


class ContainerDeleteError(RuntimeAdapterError):
    """Raised when ``delete_container`` cannot remove the container."""


class NetnsRefError(RuntimeAdapterError):
    """Raised when ``get_container_netns_ref`` cannot resolve pid or netns (container missing or not runnable)."""
