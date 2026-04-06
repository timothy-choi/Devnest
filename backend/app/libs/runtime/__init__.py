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
    BindMountInfo,
    ContainerInspectionResult,
    EnsureRunningRuntimeResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
    CODE_SERVER_CONFIG_CONTAINER_PATH,
    CODE_SERVER_DATA_CONTAINER_PATH,
    WORKSPACE_IDE_CONTAINER_PORT,
    WORKSPACE_PROJECT_CONTAINER_PATH,
    WorkspaceExtraBindMountSpec,
    WorkspaceProjectMountSpec,
)
from .runtime_orchestrator import ensure_running_runtime_only

__all__ = [
    "BindMountInfo",
    "CODE_SERVER_CONFIG_CONTAINER_PATH",
    "CODE_SERVER_DATA_CONTAINER_PATH",
    "ContainerCreateError",
    "DockerRuntimeAdapter",
    "WORKSPACE_IDE_CONTAINER_PORT",
    "WORKSPACE_PROJECT_CONTAINER_PATH",
    "WorkspaceExtraBindMountSpec",
    "WorkspaceProjectMountSpec",
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
