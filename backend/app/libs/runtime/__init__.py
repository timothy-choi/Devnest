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
    EnsureRunningRuntimeResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
)
from .runtime_orchestrator import ensure_running_runtime_only

__all__ = [
    "ContainerCreateError",
    "DockerRuntimeAdapter",
    "EnsureRunningRuntimeResult",
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
    "ensure_running_runtime_only",
]
