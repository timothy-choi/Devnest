"""V1 placement defaults (until workspace config drives CPU/memory requests)."""

# Conservative workspace-shaped request for placement fit checks and ``WorkspaceRuntime`` reservation.
DEFAULT_WORKSPACE_REQUEST_CPU = 1.0
DEFAULT_WORKSPACE_REQUEST_MEMORY_MB = 512
