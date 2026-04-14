"""Result types returned by the orchestrator service.

``success`` roll-up semantics (V1):

- **BringUp / health check:** ``success`` mirrors probe ``healthy`` when the container is running;
  missing/stopped containers yield ``success=False`` with ``issues`` (health check path).
- **Stop:** ``success`` requires the engine stop to succeed; ``topology_detached=False`` is a
  failure only when ``issues`` contain ``topology:detach_failed:`` (idempotent detach no-ops do
  not add that prefix).
- **Delete:** ``success`` requires ``container_deleted`` **and**
  ``topology_detached is not False`` (detach idempotent ``False`` fails the roll-up).
- **Restart / update (restart path):** ``success`` follows the bring-up probe roll-up after a
  successful stop roll-up; partial failures set ``stop_success`` / ``bringup_success`` accordingly.
- **Update (noop):** ``success`` follows the probe roll-up when the container is running.

``issues`` use stable ``component:code:message``-style strings where applicable; the service
normalizes empty issue lists to ``None`` for a stable JSON/API shape.

**Bring-up rollback:** On probe failure or aborted bring-up, the orchestrator runs a compensating
stop/detach/IP release. ``rollback_*`` fields describe that attempt; the worker maps a failed
rollback to ``WorkspaceRuntime.health_status=CLEANUP_REQUIRED`` for deterministic follow-up.
"""

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
    # Compensating rollback after failed bring-up (probe unhealthy or exception path in caller).
    rollback_attempted: bool = False
    rollback_succeeded: Optional[bool] = None
    rollback_issues: Optional[List[str]] = None


@dataclass
class WorkspaceStopResult:
    workspace_id: str
    success: bool
    container_id: Optional[str] = None
    container_state: Optional[str] = None
    topology_detached: Optional[bool] = None
    issues: Optional[List[str]] = None


@dataclass
class WorkspaceDeleteResult:
    workspace_id: str
    success: bool
    container_deleted: Optional[bool] = None
    topology_detached: Optional[bool] = None
    topology_deleted: Optional[bool] = None
    container_id: Optional[str] = None
    issues: Optional[List[str]] = None


@dataclass
class WorkspaceRestartResult:
    workspace_id: str
    success: bool
    stop_success: Optional[bool] = None
    bringup_success: Optional[bool] = None
    container_id: Optional[str] = None
    container_state: Optional[str] = None
    node_id: Optional[str] = None
    topology_id: Optional[str] = None
    workspace_ip: Optional[str] = None
    internal_endpoint: Optional[str] = None
    probe_healthy: Optional[bool] = None
    issues: Optional[List[str]] = None


@dataclass
class WorkspaceUpdateResult:
    workspace_id: str
    success: bool
    current_config_version: int = 0
    requested_config_version: int = 0
    update_strategy: Optional[str] = None
    no_op: bool = False
    stop_success: Optional[bool] = None
    bringup_success: Optional[bool] = None
    container_id: Optional[str] = None
    container_state: Optional[str] = None
    node_id: Optional[str] = None
    topology_id: Optional[str] = None
    workspace_ip: Optional[str] = None
    internal_endpoint: Optional[str] = None
    probe_healthy: Optional[bool] = None
    issues: Optional[List[str]] = None


@dataclass
class WorkspaceSnapshotOperationResult:
    """Result of orchestrator filesystem export/import for workspace snapshots (V1 tar.gz)."""

    workspace_id: str
    success: bool
    size_bytes: Optional[int] = None
    issues: Optional[List[str]] = None
