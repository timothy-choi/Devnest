"""Construct a real :class:`DefaultOrchestratorService` for API / worker execution (request-scoped DB session).

Uses :mod:`app.services.node_execution_service` to bind Docker + Linux commands to the placed
``ExecutionNode`` (local engine, ``ssh_docker``, or ``ssm_docker``). Topology persistence stays
:class:`DbTopologyAdapter`; probes use :class:`DefaultProbeRunner` (remote checks when a runner is set).

Image and paths are configurable via settings / env; see :func:`build_default_orchestrator_for_session`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlmodel import Session

from app.libs.common.config import get_settings
from app.libs.probes.probe_runner import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter
from app.services.node_execution_service import resolve_node_execution_bundle
from app.services.node_execution_service.errors import NodeExecutionBindingError
from app.services.workspace_service.models import Workspace, WorkspaceJob

from .errors import AppOrchestratorBindingError
from .service import DefaultOrchestratorService


def build_default_orchestrator_for_session(
    session: Session,
    *,
    execution_node_key: str | None = None,
    topology_id: int | None = None,
) -> DefaultOrchestratorService:
    """
    Build orchestrator wired to ``session`` for topology persistence.

    When ``execution_node_key`` / ``topology_id`` are omitted, values come from
    ``DEVNEST_NODE_ID`` / ``DEVNEST_TOPOLOGY_ID`` (legacy single-process dev). Docker and topology
    commands still use the local host unless a matching ``ExecutionNode`` row selects a remote mode.

    For ``ssh_docker`` / ``ssm_docker``, ``workspace_projects_base`` must be an **absolute path on the
    remote Docker host**; workspace dirs are created there via SSH or SSM respectively.

    Raises:
        AppOrchestratorBindingError: if Docker / SSH / SSM binding fails.
    """
    try:
        bundle = resolve_node_execution_bundle(session, execution_node_key)
    except NodeExecutionBindingError as e:
        raise AppOrchestratorBindingError(str(e)) from e

    settings = get_settings()
    image = (settings.workspace_container_image or "").strip()
    if not image:
        image = (os.environ.get("DEVNEST_WORKSPACE_CONTAINER_IMAGE", "") or "").strip()
    if not image:
        # Align with docker_runtime.DockerRuntimeAdapter (DEVNEST_WORKSPACE_IMAGE → devnest/workspace:latest).
        image = (os.environ.get("DEVNEST_WORKSPACE_IMAGE", "") or "").strip()
    if not image:
        image = "devnest/workspace:latest"

    base = (settings.workspace_projects_base or "").strip()
    if not base:
        base = str(Path(tempfile.gettempdir()) / "devnest-workspaces")

    if topology_id is None:
        topology_id_raw = os.environ.get("DEVNEST_TOPOLOGY_ID", "1").strip()
        try:
            topology_id = int(topology_id_raw, 10)
        except ValueError:
            topology_id = 1

    node_id = (execution_node_key or "").strip() if execution_node_key else ""
    if not node_id:
        node_id = (os.environ.get("DEVNEST_NODE_ID", "node-1") or "").strip() or "node-1"

    if bundle.runtime_adapter is not None:
        runtime = bundle.runtime_adapter
    else:
        if bundle.docker_client is None:
            raise AppOrchestratorBindingError(
                "node execution bundle has no runtime_adapter and no docker_client",
            )
        runtime = DockerRuntimeAdapter(client=bundle.docker_client)
    topology = DbTopologyAdapter(session, command_runner=bundle.topology_command_runner)
    probe = DefaultProbeRunner(
        runtime=runtime,
        topology=topology,
        service_reachability_runner=bundle.service_reachability_runner,
    )

    return DefaultOrchestratorService(
        runtime,
        topology,
        probe,
        topology_id=topology_id,
        node_id=node_id,
        workspace_projects_base=base,
        workspace_image=image,
        ensure_workspace_project_dir=bundle.ensure_workspace_project_dir,
    )


def build_orchestrator_for_workspace_job(session: Session, ws: Workspace, job: WorkspaceJob) -> DefaultOrchestratorService:
    """
    Build orchestrator using placement resolution for this workspace job.

    See :func:`app.services.placement_service.resolve_orchestrator_placement`.
    """
    from app.services.placement_service import resolve_orchestrator_placement

    node_key, tid = resolve_orchestrator_placement(session, ws, job)
    return build_default_orchestrator_for_session(
        session,
        execution_node_key=node_key,
        topology_id=tid,
    )
