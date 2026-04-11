"""Default orchestrator: coordinates runtime, topology, and probes for workspace bring-up."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.errors import RuntimeAdapterError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT, ContainerInspectionResult
from app.libs.runtime.runtime_orchestrator import ensure_running_runtime_only
from app.libs.topology.errors import TopologyDeleteError, TopologyError
from app.libs.topology.interfaces import TopologyAdapter

from .errors import (
    WorkspaceBringUpError,
    WorkspaceDeleteError,
    WorkspaceRestartError,
    WorkspaceStopError,
    WorkspaceUpdateError,
)
from .interfaces import OrchestratorService
from .results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceRestartResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)

# Docker container name: start with alphanumeric; allow [a-zA-Z0-9_.-]
_CONTAINER_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
# Persisted on the workspace container when ``requested_config_version`` is supplied at bring-up / restart.
_CONFIG_VERSION_LABEL = "devnest.config_version"


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


def _issues_or_none(issues: list[str]) -> list[str] | None:
    cleaned = [str(x).strip() for x in issues if str(x).strip()]
    return cleaned or None


def _label_value(labels: tuple[tuple[str, str], ...], key: str) -> str | None:
    for k, v in labels:
        if k == key:
            return v
    return None


def _config_version_from_inspection(ins: ContainerInspectionResult) -> int:
    """Effective config version from engine labels; ``0`` when missing or container absent (V1 baseline)."""
    if not ins.exists:
        return 0
    raw = _label_value(ins.labels, _CONFIG_VERSION_LABEL)
    if raw is None or not str(raw).strip():
        return 0
    try:
        v = int(str(raw).strip(), 10)
    except ValueError:
        return 0
    return max(0, v)


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
        # TODO: reconcile with persisted Workspace_runtime row; container label is the V1 source of truth.

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

        labels: dict[str, str] = {
            "devnest.workspace_id": wid,
            "devnest.managed_by": "orchestrator",
        }
        if requested_config_version is not None:
            labels[_CONFIG_VERSION_LABEL] = str(int(requested_config_version))

        try:
            running = ensure_running_runtime_only(
                self._runtime_adapter,
                name=name,
                image=self._workspace_image,
                ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
                labels=labels,
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

    def stop_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
    ) -> WorkspaceStopResult:
        _ = requested_by  # TODO: persist audit trail / emit stop event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceStopError("workspace_id is empty")

        ws_int = _parse_topology_workspace_id(wid)
        container_ref = _sanitize_container_name(wid)

        issues: list[str] = []

        # 1) Load current runtime state (no DB model yet; inspect by deterministic container name).
        # TODO: load persisted runtime placement (container_id/node_id/topology_id) from Workspace_runtime.
        try:
            ins = self._runtime_adapter.inspect_container(container_id=container_ref)
        except Exception as e:
            raise WorkspaceStopError(f"inspect_container failed: {e}") from e

        container_id = (ins.container_id or container_ref).strip() or None
        container_state_before = (ins.container_state or "").strip() or None

        # 2) Detach from topology (best-effort; stop can still proceed even if detach fails).
        topology_detached: bool | None = None
        try:
            det = self._topology_service.detach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ws_int,
            )
            topology_detached = bool(det.detached)
        except TopologyError as e:
            topology_detached = False
            issues.append(f"topology:detach_failed:{e}")
        except Exception as e:
            raise WorkspaceStopError(f"unexpected detach failure: {e}") from e

        # 3) Stop container (best-effort).
        stopped_state: str | None = None
        stop_success: bool = False
        if container_id is None:
            issues.append("runtime:container_id_missing")
        else:
            try:
                stop_res = self._runtime_adapter.stop_container(container_id=container_id)
                stop_success = bool(stop_res.success)
                stopped_state = (stop_res.container_state or "").strip() or None
                if not stop_res.success:
                    issues.append(f"runtime:stop_failed:{stop_res.message or 'stop_container returned success=False'}")
            except RuntimeAdapterError as e:
                issues.append(f"runtime:stop_failed:{e}")
            except Exception as e:
                raise WorkspaceStopError(f"unexpected stop failure: {e}") from e

        # TODO: persist runtime stop outcome (container_state, timestamps) to Workspace_runtime.

        success = bool(stop_success and topology_detached is not False)
        return WorkspaceStopResult(
            workspace_id=wid,
            success=success,
            container_id=container_id,
            container_state=stopped_state or container_state_before,
            topology_detached=topology_detached,
            issues=_issues_or_none(issues),
        )

    def delete_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
    ) -> WorkspaceDeleteResult:
        _ = requested_by  # TODO: persist audit trail / emit delete event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceDeleteError("workspace_id is empty")

        try:
            ws_int = _parse_topology_workspace_id(wid)
        except WorkspaceBringUpError as e:
            raise WorkspaceDeleteError(str(e)) from e

        container_ref = _sanitize_container_name(wid)
        issues: list[str] = []

        # 1) Load current runtime state (deterministic container name until Workspace_runtime exists).
        # TODO: load persisted container_id / placement from Workspace_runtime.
        try:
            ins = self._runtime_adapter.inspect_container(container_id=container_ref)
        except Exception as e:
            raise WorkspaceDeleteError(f"inspect_container failed: {e}") from e

        container_id = (ins.container_id or container_ref).strip() or None

        # 2) Detach workspace from topology (best-effort; delete_container can still run).
        topology_detached: bool | None = None
        try:
            det = self._topology_service.detach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ws_int,
            )
            topology_detached = bool(det.detached)
        except TopologyError as e:
            topology_detached = False
            issues.append(f"topology:detach_failed:{e}")
        except Exception as e:
            raise WorkspaceDeleteError(f"unexpected detach failure: {e}") from e

        # 3) Remove container from the runtime engine.
        container_deleted = False
        final_state: str | None = None
        if container_id is None:
            issues.append("runtime:container_id_missing")
        else:
            try:
                del_res = self._runtime_adapter.delete_container(container_id=container_id)
                container_deleted = bool(del_res.success)
                final_state = (del_res.container_state or "").strip() or None
                if not del_res.success:
                    issues.append(
                        f"runtime:delete_failed:{del_res.message or 'delete_container returned success=False'}",
                    )
            except RuntimeAdapterError as e:
                issues.append(f"runtime:delete_failed:{e}")
            except Exception as e:
                raise WorkspaceDeleteError(f"unexpected delete failure: {e}") from e

        # 4) Optionally remove node-local topology runtime if the adapter considers it safe
        # (e.g. no non-DETACHED attachments remain on this node).
        topology_deleted: bool | None = False
        try:
            self._topology_service.delete_topology(
                topology_id=self._topology_id,
                node_id=self._node_id,
            )
            topology_deleted = True
        except TopologyDeleteError as e:
            topology_deleted = False
            issues.append(f"topology:delete_failed:{e}")
        except Exception as e:
            raise WorkspaceDeleteError(f"unexpected topology delete failure: {e}") from e

        # TODO: persist Workspace_runtime tombstone / gateway deregistration.

        success = bool(container_deleted and topology_detached is not False)
        return WorkspaceDeleteResult(
            workspace_id=wid,
            success=success,
            container_deleted=container_deleted,
            topology_detached=topology_detached,
            topology_deleted=topology_deleted,
            container_id=container_id,
            issues=_issues_or_none(issues),
        )

    def restart_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
        requested_config_version: int | None = None,
    ) -> WorkspaceRestartResult:
        _ = requested_by  # TODO: persist audit trail / emit restart event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceRestartError("workspace_id is empty")
        try:
            _parse_topology_workspace_id(wid)
        except WorkspaceBringUpError as e:
            raise WorkspaceRestartError(str(e)) from e

        try:
            stop_res = self.stop_workspace_runtime(workspace_id=wid, requested_by=requested_by)
        except WorkspaceStopError as e:
            raise WorkspaceRestartError(str(e)) from e

        issues: list[str] = []
        if stop_res.issues:
            issues.extend(stop_res.issues)

        tid = str(self._topology_id)
        nid = self._node_id

        if not stop_res.success:
            return WorkspaceRestartResult(
                workspace_id=wid,
                success=False,
                stop_success=False,
                bringup_success=False,
                container_id=stop_res.container_id,
                container_state=stop_res.container_state,
                node_id=nid,
                topology_id=tid,
                workspace_ip=None,
                internal_endpoint=None,
                probe_healthy=None,
                issues=_issues_or_none(issues),
            )

        try:
            up_res = self.bring_up_workspace_runtime(
                workspace_id=wid,
                requested_config_version=requested_config_version,
            )
        except WorkspaceBringUpError as e:
            issues.append(f"bringup:failed:{e}")
            return WorkspaceRestartResult(
                workspace_id=wid,
                success=False,
                stop_success=True,
                bringup_success=False,
                container_id=stop_res.container_id,
                container_state=stop_res.container_state,
                node_id=nid,
                topology_id=tid,
                workspace_ip=None,
                internal_endpoint=None,
                probe_healthy=None,
                issues=_issues_or_none(issues),
            )

        if up_res.issues:
            issues.extend(up_res.issues)

        # TODO: persist Workspace_runtime restart outcome (timestamps, container_id, probe result).

        return WorkspaceRestartResult(
            workspace_id=wid,
            success=bool(up_res.success),
            stop_success=True,
            bringup_success=bool(up_res.success),
            container_id=up_res.container_id,
            container_state=up_res.container_state,
            node_id=up_res.node_id or nid,
            topology_id=up_res.topology_id or tid,
            workspace_ip=up_res.workspace_ip,
            internal_endpoint=up_res.internal_endpoint,
            probe_healthy=up_res.probe_healthy,
            issues=_issues_or_none(issues),
        )

    def update_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int,
        requested_by: str | None = None,
    ) -> WorkspaceUpdateResult:
        _ = requested_by  # TODO: persist audit trail / emit update event

        if requested_config_version < 0:
            raise WorkspaceUpdateError("requested_config_version must be non-negative")

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceUpdateError("workspace_id is empty")
        try:
            _parse_topology_workspace_id(wid)
        except WorkspaceBringUpError as e:
            raise WorkspaceUpdateError(str(e)) from e

        container_ref = _sanitize_container_name(wid)
        try:
            ins = self._runtime_adapter.inspect_container(container_id=container_ref)
        except Exception as e:
            raise WorkspaceUpdateError(f"inspect_container failed: {e}") from e

        current = _config_version_from_inspection(ins)
        tid = str(self._topology_id)
        nid = self._node_id

        if current == requested_config_version:
            return self._update_workspace_runtime_noop(
                wid=wid,
                ins=ins,
                container_ref=container_ref,
                requested_config_version=requested_config_version,
                current_config_version=current,
                node_id=nid,
                topology_id=tid,
            )

        try:
            r = self.restart_workspace_runtime(
                workspace_id=wid,
                requested_by=requested_by,
                requested_config_version=requested_config_version,
            )
        except WorkspaceRestartError as e:
            raise WorkspaceUpdateError(str(e)) from e

        issues: list[str] = []
        if r.issues:
            issues.extend(r.issues)

        # TODO: persist applied config version to Workspace_runtime when DB model exists.

        return WorkspaceUpdateResult(
            workspace_id=wid,
            success=bool(r.success),
            current_config_version=current,
            requested_config_version=requested_config_version,
            update_strategy="restart",
            no_op=False,
            stop_success=r.stop_success,
            bringup_success=r.bringup_success,
            container_id=r.container_id,
            container_state=r.container_state,
            node_id=r.node_id,
            topology_id=r.topology_id,
            workspace_ip=r.workspace_ip,
            internal_endpoint=r.internal_endpoint,
            probe_healthy=r.probe_healthy,
            issues=_issues_or_none(issues),
        )

    def _update_workspace_runtime_noop(
        self,
        *,
        wid: str,
        ins: ContainerInspectionResult,
        container_ref: str,
        requested_config_version: int,
        current_config_version: int,
        node_id: str,
        topology_id: str,
    ) -> WorkspaceUpdateResult:
        """Version already matches; snapshot health without stop/bring-up."""
        issues: list[str] = []
        if not ins.exists:
            issues.append("update:noop:workspace_runtime_not_found")
            return WorkspaceUpdateResult(
                workspace_id=wid,
                success=False,
                current_config_version=current_config_version,
                requested_config_version=requested_config_version,
                update_strategy="noop",
                no_op=True,
                node_id=node_id,
                topology_id=topology_id,
                issues=_issues_or_none(issues),
            )

        state = (ins.container_state or "").strip().lower()
        if state != "running":
            issues.append(f"update:noop:container_not_running:{state or 'unknown'}")
            cid = (ins.container_id or container_ref).strip() or None
            return WorkspaceUpdateResult(
                workspace_id=wid,
                success=False,
                current_config_version=current_config_version,
                requested_config_version=requested_config_version,
                update_strategy="noop",
                no_op=True,
                container_id=cid,
                container_state=ins.container_state,
                node_id=node_id,
                topology_id=topology_id,
                issues=_issues_or_none(issues),
            )

        cid = (ins.container_id or container_ref).strip()
        if not cid:
            issues.append("update:noop:container_id_missing")
            return WorkspaceUpdateResult(
                workspace_id=wid,
                success=False,
                current_config_version=current_config_version,
                requested_config_version=requested_config_version,
                update_strategy="noop",
                no_op=True,
                container_state=ins.container_state,
                node_id=node_id,
                topology_id=topology_id,
                issues=_issues_or_none(issues),
            )

        try:
            health = self._probe_runner.check_workspace_health(
                workspace_id=wid,
                topology_id=topology_id,
                node_id=node_id,
                container_id=cid,
                expected_port=WORKSPACE_IDE_CONTAINER_PORT,
                timeout_seconds=5.0,
            )
        except Exception as e:
            raise WorkspaceUpdateError(f"probe health check failed: {e}") from e

        if health.issues:
            issues.extend([f"{i.component}:{i.code}:{i.message}" for i in health.issues])

        return WorkspaceUpdateResult(
            workspace_id=wid,
            success=bool(health.healthy),
            current_config_version=current_config_version,
            requested_config_version=requested_config_version,
            update_strategy="noop",
            no_op=True,
            container_id=cid,
            container_state=health.container_state or ins.container_state,
            node_id=node_id,
            topology_id=topology_id,
            workspace_ip=health.workspace_ip,
            internal_endpoint=health.internal_endpoint,
            probe_healthy=health.healthy,
            issues=_issues_or_none(issues),
        )
