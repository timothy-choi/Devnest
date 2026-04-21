"""V1 placement defaults (until workspace config drives CPU/memory requests)."""

# Conservative workspace-shaped request for placement fit checks and ``WorkspaceRuntime`` reservation.
DEFAULT_WORKSPACE_REQUEST_CPU = 1.0
DEFAULT_WORKSPACE_REQUEST_MEMORY_MB = 512
DEFAULT_WORKSPACE_REQUEST_DISK_MB = 4096

# Conservative execution-node defaults for bootstrap / existing rows.
DEFAULT_EXECUTION_NODE_MAX_WORKSPACES = 32
DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB = 102_400
