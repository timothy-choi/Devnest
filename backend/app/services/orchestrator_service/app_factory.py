"""Construct a real :class:`DefaultOrchestratorService` for API / worker execution (request-scoped DB session).

Uses Docker runtime + :class:`DbTopologyAdapter` + :class:`DefaultProbeRunner`. Linux bridge/attachment
follow the same env defaults as ``DbTopologyAdapter`` (e.g. ``DEVNEST_TOPOLOGY_SKIP_LINUX_*``).

Image and paths are configurable via settings / env; see :func:`build_default_orchestrator_for_session`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import docker
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.workspace_service.models import Workspace, WorkspaceJob
from app.libs.probes.probe_runner import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter

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
    ``DEVNEST_NODE_ID`` / ``DEVNEST_TOPOLOGY_ID`` (legacy single-process dev).

    Raises:
        AppOrchestratorBindingError: if Docker is not available or misconfigured.
    """
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        raise AppOrchestratorBindingError(
            f"Docker engine not available for workspace orchestrator: {e}",
        ) from e

    settings = get_settings()
    image = (settings.workspace_container_image or "").strip()
    if not image:
        image = (os.environ.get("DEVNEST_WORKSPACE_CONTAINER_IMAGE", "") or "").strip()
    if not image:
        image = "nginx:alpine"

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

    runtime = DockerRuntimeAdapter(client=client)
    topology = DbTopologyAdapter(session)
    probe = DefaultProbeRunner(runtime=runtime, topology=topology)

    return DefaultOrchestratorService(
        runtime,
        topology,
        probe,
        topology_id=topology_id,
        node_id=node_id,
        workspace_projects_base=base,
        workspace_image=image,
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
