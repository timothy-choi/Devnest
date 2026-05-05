"""V1 placement defaults (until workspace config drives CPU/memory requests)."""

# Conservative workspace-shaped request for placement fit checks and ``WorkspaceRuntime`` reservation.
DEFAULT_WORKSPACE_REQUEST_CPU = 1.0
DEFAULT_WORKSPACE_REQUEST_MEMORY_MB = 512
DEFAULT_WORKSPACE_REQUEST_DISK_MB = 4096
DEFAULT_WORKSPACE_REQUEST_SLOTS = 1

# Conservative execution-node defaults for bootstrap / existing rows.
DEFAULT_EXECUTION_NODE_MAX_WORKSPACES = 32
DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB = 102_400


def default_workspace_requested_cpu() -> float:
    from app.libs.common.config import get_settings

    return float(get_settings().devnest_workspace_required_cpu)


def default_workspace_requested_memory_mb() -> int:
    from app.libs.common.config import get_settings

    return int(get_settings().devnest_workspace_required_memory_mb)


def default_workspace_requested_disk_mb() -> int:
    from app.libs.common.config import get_settings

    return int(get_settings().devnest_workspace_required_disk_mb)


def default_workspace_requested_slots() -> int:
    from app.libs.common.config import get_settings

    return int(get_settings().devnest_workspace_required_slots)
