"""Runtime adapter exceptions (orchestrator may map these to workspace/job errors)."""


class RuntimeAdapterError(Exception):
    """Base class for container runtime failures surfaced by a ``RuntimeAdapter`` implementation."""


class ContainerNotFoundError(RuntimeAdapterError):
    """No container matches the given id (or name) for this adapter."""


class ContainerCreateError(RuntimeAdapterError):
    """Creating or configuring a container failed (e.g. pull, resource limits, invalid spec)."""


class ContainerStartError(RuntimeAdapterError):
    """Starting an existing container failed."""


class ContainerStopError(RuntimeAdapterError):
    """Stopping a container failed."""


class ContainerDeleteError(RuntimeAdapterError):
    """Removing a container failed."""


class NetnsRefError(RuntimeAdapterError):
    """Resolving the container network namespace reference (e.g. pid / netns path) failed."""
