"""Workspace HTTP routers."""

from .internal_gateway_auth import router as internal_gateway_auth_router
from .internal_workspace_jobs import router as internal_workspace_jobs_router
from .internal_workspace_reconcile import router as internal_workspace_reconcile_router
from .workspace_snapshots import (
    snapshots_router,
    workspace_snapshots_router,
)
from .workspaces import router as workspaces_router

__all__ = [
    "internal_gateway_auth_router",
    "internal_workspace_jobs_router",
    "internal_workspace_reconcile_router",
    "snapshots_router",
    "workspace_snapshots_router",
    "workspaces_router",
]
