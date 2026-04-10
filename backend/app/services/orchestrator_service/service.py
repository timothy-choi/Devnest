"""Default orchestrator: coordinates runtime, topology, and probes for workspace bring-up."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.errors import RuntimeAdapterError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.runtime.runtime_orchestrator import ensure_running_runtime_only
from app.libs.topology.errors import TopologyError
from app.libs.topology.interfaces import TopologyAdapter

from .errors import WorkspaceBringUpError
from .interfaces import OrchestratorService
from .results import WorkspaceBringUpResult

# Docker container name: start with alphanumeric; allow [a-zA-Z0-9_.-]
_CONTAINER_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sanitize_container_name(workspace_id: str) -> str:
    raw = _CONTAINER_NAME_RE.sub("-", workspace_id.strip())
    if not raw or not (raw[0].isalnum()):
        raw = f"w{raw}" if raw else "workspace"
    return f"devnest-ws-{raw}"[:120]


def _parse_topology_workspace_id(workspace_id: str) -> int:
    s = workspace_id.strip()
    try:
        v = int(s, 10)
    except ValueError as e:
        raise WorkspaceBringUpError(
            f"workspace_id must be a base-10 integer for V1 topology allocation: {workspace_id!r}",
        ) from e
    if v < 0:
        raise WorkspaceBringUpError(f"workspace_id must be non-negative: {v}")
    return v


class DefaultOrchestratorService(OrchestratorService):
    """
    Coordinates ``RuntimeAdapter``, ``TopologyAdapter``, and ``ProbeRunner`` for bring-up.

    Uses :func:`app.libs.runtime.runtime_orchestrator.ensure_running_runtime_only` for the
    runtime sequence (no duplicated ensure/start/netns logic).

    Placement (``topology_id``, ``node_id``, host project directory) is injected until the
    workspace service persists intent and scheduler assigns nodes.
    """

    def __init__(
        self,
        runtime_adapter: RuntimeAdapter,
        topology_service: TopologyAdapter,
        probe_runner: ProbeRunner,
        *,
        topology_id: int = 1,
        node_id: str = "node-1",
        workspace_projects_base: str | None = None,
        workspace_image: str | None = None,
    ) -> None:
        self._runtime_adapter = runtime_adapter
        self._topology_service = topology_service
        self._probe_runner = probe_runner
        self._topology_id = topology_id
        self._node_id = node_id.strip() or "node-1"
        self._workspace_projects_base = workspace_projects_base or os.path.join(
            tempfile.gettempdir(),
            "devnest-workspaces",
        )
        self._workspace_image = workspace_image

    def bring_up_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int | None = None,
    ) -> WorkspaceBringUpResult:
        _ = requested_config_version  # TODO: reconcile with persisted Workspace_runtime row / config version

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceBringUpError("workspace_id is empty")

        ws_int = _parse_topology_workspace_id(wid)
        name = _sanitize_container_name(wid)
        host_dir = Path(self._workspace_projects_base).resolve() / wid
        try:
            host_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise WorkspaceBringUpError(f"cannot create workspace project directory {host_dir}: {e}") from e

        try:
            running = ensure_running_runtime_only(
                self._runtime_adapter,
                name=name,
                image=self._workspace_image,
                ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
                labels={
                    "devnest.workspace_id": wid,
                    "devnest.managed_by": "orchestrator",
                },
                workspace_host_path=str(host_dir),
            )
        except RuntimeAdapterError as e:
            raise WorkspaceBringUpError(f"runtime bring-up failed: {e}") from e

        # TODO: persist container_id, image, ports, paths to Workspace_runtime (DB).

        try:
            self._topology_service.ensure_node_topology(
                topology_id=self._topology_id,
                node_id=self._node_id,
            )
            ip_res = self._topology_service.allocate_workspace_ip(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ws_int,
            )
            netns = self._runtime_adapter.get_container_netns_ref(container_id=running.container_id)
            attach_res = self._topology_service.attach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ws_int,
                container_id=running.container_id,
                netns_ref=netns.netns_ref,
                workspace_ip=ip_res.workspace_ip,
            )
        except TopologyError as e:
            raise WorkspaceBringUpError(f"topology bring-up failed: {e}") from e

        # TODO: register attach_res.internal_endpoint with edge gateway / route tables.

        try:
            health = self._probe_runner.check_workspace_health(
                workspace_id=wid,
                topology_id=str(self._topology_id),
                node_id=self._node_id,
                container_id=running.container_id,
                expected_port=WORKSPACE_IDE_CONTAINER_PORT,
                timeout_seconds=5.0,
            )
        except Exception as e:
            raise WorkspaceBringUpError(f"probe health check failed: {e}") from e

        issue_msgs: list[str] | None
        if health.issues:
            issue_msgs = [f"{i.component}:{i.code}:{i.message}" for i in health.issues]
        else:
            issue_msgs = None

        return WorkspaceBringUpResult(
            workspace_id=wid,
            success=health.healthy,
            node_id=self._node_id,
            topology_id=str(self._topology_id),
            container_id=running.container_id,
            container_state=health.container_state or running.container_state,
            netns_ref=netns.netns_ref,
            workspace_ip=health.workspace_ip or attach_res.workspace_ip,
            internal_endpoint=health.internal_endpoint or attach_res.internal_endpoint,
            probe_healthy=health.healthy,
            issues=issue_msgs,
        )
