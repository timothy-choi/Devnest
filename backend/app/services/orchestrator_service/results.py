"""Result types returned by the orchestrator service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class WorkspaceBringUpResult:
    workspace_id: str
    success: bool
    node_id: Optional[str] = None
    topology_id: Optional[str] = None
    container_id: Optional[str] = None
    container_state: Optional[str] = None
    netns_ref: Optional[str] = None
    workspace_ip: Optional[str] = None
    internal_endpoint: Optional[str] = None
    probe_healthy: Optional[bool] = None
    issues: Optional[List[str]] = None
