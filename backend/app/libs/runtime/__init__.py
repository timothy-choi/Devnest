"""Runtime adapter: normalized types for container lifecycle (orchestrator-facing)."""

from .docker_runtime import DockerRuntimeAdapter
from .errors import (
    ContainerCreateError,
    ContainerDeleteError,
    ContainerNotFoundError,
    ContainerStartError,
    ContainerStopError,
    NetnsRefError,
    RuntimeAdapterError,
)
from .interfaces import RuntimeAdapter
from .models import (
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
)

__all__ = [
    "ContainerCreateError",
    "DockerRuntimeAdapter",
    "ContainerDeleteError",
    "ContainerInspectionResult",
    "ContainerNotFoundError",
    "ContainerStartError",
    "ContainerStopError",
    "NetnsRefError",
    "NetnsRefResult",
    "RuntimeActionResult",
    "RuntimeAdapter",
    "RuntimeAdapterError",
    "RuntimeEnsureResult",
]
