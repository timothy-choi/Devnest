"""Default orchestrator: coordinates ``RuntimeAdapter``, ``TopologyAdapter``, and ``ProbeRunner``.

Mutating flows: bring-up, stop, delete, restart, update (noop or restart-based). Read-only:
``check_workspace_runtime_health``. Placement (``topology_id``, ``node_id``, project base) is
injected until scheduler / ``Workspace_runtime`` persistence exist.

**Persistence boundary:** This package does not write ``Workspace`` / ``WorkspaceRuntime`` / ``WorkspaceJob``
rows. Callers (typically :mod:`app.workers.workspace_job_worker.worker`) persist orchestration outcomes.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.libs.observability.log_events import LogEvent, log_event
from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.errors import RuntimeAdapterError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import (
    WORKSPACE_IDE_CONTAINER_PORT,
    ContainerInspectionResult,
    EnsureRunningRuntimeResult,
    NetnsRefResult,
)
from app.libs.runtime.runtime_orchestrator import ensure_running_runtime_only
from app.libs.topology.errors import TopologyDeleteError, TopologyError
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.results import AttachWorkspaceResult

from app.services.node_execution_service.workspace_project_dir import (
    default_local_ensure_workspace_project_dir,
)

from .errors import (
    WorkspaceBringUpError,
    WorkspaceDeleteError,
    WorkspaceRestartError,
    WorkspaceSnapshotError,
    WorkspaceStopError,
    WorkspaceUpdateError,
)
from .interfaces import OrchestratorService
from .results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceRestartResult,
    WorkspaceSnapshotOperationResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)

# Docker container name: start with alphanumeric; allow [a-zA-Z0-9_.-]
_CONTAINER_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
# Persisted on the workspace container when ``requested_config_version`` is supplied at bring-up / restart.
_CONFIG_VERSION_LABEL = "devnest.config_version"

logger = logging.getLogger(__name__)

_EnsureWorkspaceProjectDir = Callable[[str, str], str]


def _env_skip_linux_topology_attachment() -> bool:
    """True when ``DbTopologyAdapter`` skips Linux veth wiring (same truthiness as adapter env check)."""
    return os.environ.get("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


@dataclass(frozen=True)
class _BringUpContext:
    """Validated bring-up inputs; ``workspace_host_path`` is on the execution host (local or remote)."""

    wid: str
    ws_int: int
    container_name: str
    workspace_host_path: str
    labels: dict[str, str]


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


def _stop_workspace_success_roll_up(
    *,
    stop_success: bool,
    topology_detached: bool | None,
    issues: list[str],
) -> bool:
    """
    True when the container reached a safe stopped/absent state (``stop_success``) and topology
    detach did not record an explicit failure.

    ``detach_workspace`` returns ``detached=False`` for idempotent no-ops (no row or already
    ``DETACHED``) without appending issues. A real detach problem adds ``topology:detach_failed:``
    and sets ``topology_detached=False``.
    """
    detach_explicit_failure = topology_detached is False and any(
        str(i).strip().startswith("topology:detach_failed:")
        for i in issues
    )
    if detach_explicit_failure:
        return False
    return bool(stop_success)


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
    Coordinates runtime, topology, and probes for workspace lifecycle operations.

    Runtime start uses :func:`app.libs.runtime.runtime_orchestrator.ensure_running_runtime_only`.
    Restart and update (non-noop) compose ``stop_workspace_runtime`` and
    ``bring_up_workspace_runtime`` without duplicating that sequence.
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
        ensure_workspace_project_dir: _EnsureWorkspaceProjectDir | None = None,
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
        self._ensure_workspace_project_dir = (
            ensure_workspace_project_dir or default_local_ensure_workspace_project_dir
        )

    def _bring_up_build_context(
        self,
        workspace_id: str,
        requested_config_version: int | None,
    ) -> _BringUpContext:
        # TODO: reconcile with persisted Workspace_runtime row; container label is the V1 source of truth.
        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceBringUpError("workspace_id is empty")

        ws_int = _parse_topology_workspace_id(wid)
        name = _sanitize_container_name(wid)
        try:
            workspace_host_path = self._ensure_workspace_project_dir(self._workspace_projects_base, wid)
        except ValueError as e:
            raise WorkspaceBringUpError(str(e)) from e

        labels: dict[str, str] = {
            "devnest.workspace_id": wid,
            "devnest.managed_by": "orchestrator",
        }
        if requested_config_version is not None:
            labels[_CONFIG_VERSION_LABEL] = str(int(requested_config_version))

        return _BringUpContext(
            wid=wid,
            ws_int=ws_int,
            container_name=name,
            workspace_host_path=workspace_host_path,
            labels=labels,
        )

    def _bring_up_start_container(self, ctx: _BringUpContext) -> EnsureRunningRuntimeResult:
        """Run ``ensure_running_runtime_only`` (ensure → start → inspect → netns unless skip-linux-attach)."""
        try:
            return ensure_running_runtime_only(
                self._runtime_adapter,
                name=ctx.container_name,
                image=self._workspace_image,
                ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
                labels=ctx.labels,
                workspace_host_path=ctx.workspace_host_path,
                skip_netns_resolution=_env_skip_linux_topology_attachment(),
            )
        except RuntimeAdapterError as e:
            raise WorkspaceBringUpError(f"runtime bring-up failed: {e}") from e

    def _bring_up_attach_topology(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult,
    ) -> tuple[NetnsRefResult, AttachWorkspaceResult]:
        """Ensure node topology, allocate IP, attach workspace veth to bridge."""
        try:
            self._topology_service.ensure_node_topology(
                topology_id=self._topology_id,
                node_id=self._node_id,
            )
            ip_res = self._topology_service.allocate_workspace_ip(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ctx.ws_int,
            )
            # When Linux veth attachment is disabled, ``ensure_running_runtime_only`` already used a
            # placeholder netns; reuse it (no second ``get_container_netns_ref``).
            if _env_skip_linux_topology_attachment():
                netns = NetnsRefResult(
                    container_id=running.container_id,
                    pid=running.pid,
                    netns_ref=running.netns_ref,
                )
            else:
                netns = self._runtime_adapter.get_container_netns_ref(container_id=running.container_id)
            attach_res = self._topology_service.attach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ctx.ws_int,
                container_id=running.container_id,
                netns_ref=netns.netns_ref,
                workspace_ip=ip_res.workspace_ip,
            )
        except TopologyError as e:
            raise WorkspaceBringUpError(f"topology bring-up failed: {e}") from e
        except RuntimeAdapterError as e:
            raise WorkspaceBringUpError(f"runtime topology handoff failed: {e}") from e
        return netns, attach_res

    def _bring_up_run_probe(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult,
        netns: NetnsRefResult,
        attach_res: AttachWorkspaceResult,
    ) -> WorkspaceBringUpResult:
        # Route registration: workspace job worker calls route-admin after RUNNING (not orchestrator).
        try:
            health = self._probe_runner.check_workspace_health(
                workspace_id=ctx.wid,
                topology_id=str(self._topology_id),
                node_id=self._node_id,
                container_id=running.container_id,
                expected_port=WORKSPACE_IDE_CONTAINER_PORT,
                timeout_seconds=5.0,
            )
        except Exception as e:
            raise WorkspaceBringUpError(f"probe health check failed: {e}") from e

        issue_msgs: list[str] = (
            [f"{i.component}:{i.code}:{i.message}" for i in health.issues] if health.issues else []
        )

        return WorkspaceBringUpResult(
            workspace_id=ctx.wid,
            success=health.healthy,
            node_id=self._node_id,
            topology_id=str(self._topology_id),
            container_id=running.container_id,
            container_state=health.container_state or running.container_state,
            netns_ref=netns.netns_ref,
            workspace_ip=health.workspace_ip or attach_res.workspace_ip,
            internal_endpoint=health.internal_endpoint or attach_res.internal_endpoint,
            probe_healthy=health.healthy,
            issues=_issues_or_none(issue_msgs),
        )

    def bring_up_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int | None = None,
    ) -> WorkspaceBringUpResult:
        """
        Start workspace container, wire topology attachment, run service probe.

        Returns a :class:`WorkspaceBringUpResult` for the worker to persist on ``WorkspaceRuntime``.
        """
        ctx = self._bring_up_build_context(workspace_id, requested_config_version)
        log_event(
            logger,
            LogEvent.ORCHESTRATOR_BRINGUP_STARTED,
            workspace_id=ctx.wid,
            requested_config_version=requested_config_version,
            topology_id=self._topology_id,
            node_id=self._node_id,
        )
        try:
            running = self._bring_up_start_container(ctx)
            logger.debug(
                "orchestrator_bring_up_runtime_running",
                extra={"workspace_id": ctx.wid, "container_id": running.container_id},
            )
            netns, attach_res = self._bring_up_attach_topology(ctx, running)
            result = self._bring_up_run_probe(ctx, running, netns, attach_res)
        except Exception as e:
            log_event(
                logger,
                LogEvent.ORCHESTRATOR_BRINGUP_FAILED,
                level=logging.WARNING,
                workspace_id=ctx.wid,
                error=str(e)[:500],
            )
            raise
        if result.success:
            log_event(
                logger,
                LogEvent.ORCHESTRATOR_BRINGUP_SUCCEEDED,
                workspace_id=ctx.wid,
                probe_healthy=result.probe_healthy,
                container_id=result.container_id,
            )
        else:
            log_event(
                logger,
                LogEvent.ORCHESTRATOR_BRINGUP_FAILED,
                level=logging.WARNING,
                workspace_id=ctx.wid,
                probe_healthy=result.probe_healthy,
                issues=(result.issues or [])[:5],
            )
        return result

    def _stop_load_inspection(
        self,
        wid: str,
        container_ref: str,
    ) -> tuple[int, str | None, str | None]:
        """Inspect container by deterministic name; return topology int id, container_id, prior state."""
        ws_int = _parse_topology_workspace_id(wid)
        # TODO: load persisted runtime placement (container_id/node_id/topology_id) from Workspace_runtime.
        try:
            ins = self._runtime_adapter.inspect_container(container_id=container_ref)
        except Exception as e:
            raise WorkspaceStopError(f"inspect_container failed: {e}") from e

        container_id = (ins.container_id or container_ref).strip() or None
        container_state_before = (ins.container_state or "").strip() or None
        return ws_int, container_id, container_state_before

    def _stop_detach_topology_best_effort(self, ws_int: int, issues: list[str]) -> bool | None:
        """Detach workspace from topology; failures become issue strings, not hard errors."""
        try:
            det = self._topology_service.detach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ws_int,
            )
            return bool(det.detached)
        except TopologyError as e:
            issues.append(f"topology:detach_failed:{e}")
            return False
        except Exception as e:
            raise WorkspaceStopError(f"unexpected detach failure: {e}") from e

    def _stop_container_best_effort(
        self,
        container_id: str | None,
        issues: list[str],
    ) -> tuple[bool, str | None]:
        """Stop container if we have an id; return (stop_success, reported container state)."""
        if container_id is None:
            issues.append("runtime:container_id_missing")
            return False, None
        try:
            stop_res = self._runtime_adapter.stop_container(container_id=container_id)
            stop_success = bool(stop_res.success)
            stopped_state = (stop_res.container_state or "").strip() or None
            if not stop_res.success:
                issues.append(
                    f"runtime:stop_failed:{stop_res.message or 'stop_container returned success=False'}",
                )
            return stop_success, stopped_state
        except RuntimeAdapterError as e:
            issues.append(f"runtime:stop_failed:{e}")
            return False, None
        except Exception as e:
            raise WorkspaceStopError(f"unexpected stop failure: {e}") from e

    def stop_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
    ) -> WorkspaceStopResult:
        """
        Detach topology (best-effort), stop container (best-effort).

        Returns :class:`WorkspaceStopResult` for the worker to persist (e.g. cleared or stopped runtime).
        """
        _ = requested_by  # TODO: persist audit trail / emit stop event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceStopError("workspace_id is empty")

        container_ref = _sanitize_container_name(wid)
        logger.info(
            "orchestrator_stop_start",
            extra={"workspace_id": wid, "topology_id": self._topology_id, "node_id": self._node_id},
        )

        issues: list[str] = []
        ws_int, container_id, container_state_before = self._stop_load_inspection(wid, container_ref)
        topology_detached = self._stop_detach_topology_best_effort(ws_int, issues)
        stop_success, stopped_state = self._stop_container_best_effort(container_id, issues)

        # TODO: persist runtime stop outcome (container_state, timestamps) to Workspace_runtime.

        success = _stop_workspace_success_roll_up(
            stop_success=stop_success,
            topology_detached=topology_detached,
            issues=issues,
        )
        result = WorkspaceStopResult(
            workspace_id=wid,
            success=success,
            container_id=container_id,
            container_state=stopped_state or container_state_before,
            topology_detached=topology_detached,
            issues=_issues_or_none(issues),
        )
        logger.info(
            "orchestrator_stop_complete",
            extra={
                "workspace_id": wid,
                "success": result.success,
                "topology_detached": result.topology_detached,
            },
        )
        return result

    def _delete_load_inspection(self, wid: str, container_ref: str) -> tuple[int, str | None]:
        """Parse workspace id, inspect container; return topology int id and engine container_id."""
        try:
            ws_int = _parse_topology_workspace_id(wid)
        except WorkspaceBringUpError as e:
            raise WorkspaceDeleteError(str(e)) from e
        # TODO: load persisted container_id / placement from Workspace_runtime.
        try:
            ins = self._runtime_adapter.inspect_container(container_id=container_ref)
        except Exception as e:
            raise WorkspaceDeleteError(f"inspect_container failed: {e}") from e

        container_id = (ins.container_id or container_ref).strip() or None
        return ws_int, container_id

    def _delete_detach_topology_best_effort(self, ws_int: int, issues: list[str]) -> bool | None:
        try:
            det = self._topology_service.detach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ws_int,
            )
            return bool(det.detached)
        except TopologyError as e:
            issues.append(f"topology:detach_failed:{e}")
            return False
        except Exception as e:
            raise WorkspaceDeleteError(f"unexpected detach failure: {e}") from e

    def _delete_container_best_effort(
        self,
        container_id: str | None,
        issues: list[str],
    ) -> tuple[bool, str | None]:
        if container_id is None:
            issues.append("runtime:container_id_missing")
            return False, None
        try:
            del_res = self._runtime_adapter.delete_container(container_id=container_id)
            container_deleted = bool(del_res.success)
            final_state = (del_res.container_state or "").strip() or None
            if not del_res.success:
                issues.append(
                    f"runtime:delete_failed:{del_res.message or 'delete_container returned success=False'}",
                )
            return container_deleted, final_state
        except RuntimeAdapterError as e:
            issues.append(f"runtime:delete_failed:{e}")
            return False, None
        except Exception as e:
            raise WorkspaceDeleteError(f"unexpected delete failure: {e}") from e

    def _delete_topology_runtime_best_effort(self, issues: list[str]) -> bool | None:
        """Remove node-local topology runtime when safe (e.g. no non-DETACHED attachments)."""
        try:
            self._topology_service.delete_topology(
                topology_id=self._topology_id,
                node_id=self._node_id,
            )
            return True
        except TopologyDeleteError as e:
            issues.append(f"topology:delete_failed:{e}")
            return False
        except Exception as e:
            raise WorkspaceDeleteError(f"unexpected topology delete failure: {e}") from e

    def delete_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
    ) -> WorkspaceDeleteResult:
        """
        Detach, delete container, optionally delete node topology runtime.

        Returns :class:`WorkspaceDeleteResult` for the worker to clear ``WorkspaceRuntime`` on success.
        """
        _ = requested_by  # TODO: persist audit trail / emit delete event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceDeleteError("workspace_id is empty")

        container_ref = _sanitize_container_name(wid)
        logger.info(
            "orchestrator_delete_start",
            extra={"workspace_id": wid, "topology_id": self._topology_id, "node_id": self._node_id},
        )

        issues: list[str] = []
        ws_int, container_id = self._delete_load_inspection(wid, container_ref)
        topology_detached = self._delete_detach_topology_best_effort(ws_int, issues)
        container_deleted, _ = self._delete_container_best_effort(container_id, issues)
        topology_deleted = self._delete_topology_runtime_best_effort(issues)

        # TODO: persist Workspace_runtime tombstone; gateway deregistration is done in the job worker.

        success = bool(container_deleted and topology_detached is not False)
        result = WorkspaceDeleteResult(
            workspace_id=wid,
            success=success,
            container_deleted=container_deleted,
            topology_detached=topology_detached,
            topology_deleted=topology_deleted,
            container_id=container_id,
            issues=_issues_or_none(issues),
        )
        logger.info(
            "orchestrator_delete_complete",
            extra={
                "workspace_id": wid,
                "success": result.success,
                "container_deleted": result.container_deleted,
                "topology_deleted": result.topology_deleted,
            },
        )
        return result

    def restart_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
        requested_config_version: int | None = None,
    ) -> WorkspaceRestartResult:
        """
        Stop then bring-up workspace runtime (optional new ``requested_config_version`` label).

        Returns :class:`WorkspaceRestartResult` aggregating stop and bring-up outcomes.
        """
        _ = requested_by  # TODO: persist audit trail / emit restart event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceRestartError("workspace_id is empty")
        try:
            _parse_topology_workspace_id(wid)
        except WorkspaceBringUpError as e:
            raise WorkspaceRestartError(str(e)) from e

        logger.info(
            "orchestrator_restart_start",
            extra={
                "workspace_id": wid,
                "requested_config_version": requested_config_version,
                "topology_id": self._topology_id,
                "node_id": self._node_id,
            },
        )

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
            logger.info(
                "orchestrator_restart_complete",
                extra={"workspace_id": wid, "success": False, "stop_success": False},
            )
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
            logger.info(
                "orchestrator_restart_complete",
                extra={"workspace_id": wid, "success": False, "stop_success": True, "bringup_success": False},
            )
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

        out = WorkspaceRestartResult(
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
        logger.info(
            "orchestrator_restart_complete",
            extra={
                "workspace_id": wid,
                "success": out.success,
                "stop_success": out.stop_success,
                "bringup_success": out.bringup_success,
            },
        )
        return out

    def update_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int,
        requested_by: str | None = None,
    ) -> WorkspaceUpdateResult:
        """
        If container config label matches ``requested_config_version``, health-check only (noop).

        Otherwise restarts the workspace to apply the new version.
        """
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
            logger.info(
                "orchestrator_update_noop",
                extra={
                    "workspace_id": wid,
                    "requested_config_version": requested_config_version,
                    "current_config_version": current,
                },
            )
            return self._update_workspace_runtime_noop(
                wid=wid,
                ins=ins,
                container_ref=container_ref,
                requested_config_version=requested_config_version,
                current_config_version=current,
                node_id=nid,
                topology_id=tid,
            )

        logger.info(
            "orchestrator_update_restart",
            extra={
                "workspace_id": wid,
                "requested_config_version": requested_config_version,
                "current_config_version": current,
            },
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

        ur = WorkspaceUpdateResult(
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
        logger.info(
            "orchestrator_update_complete",
            extra={"workspace_id": wid, "success": ur.success, "update_strategy": "restart"},
        )
        return ur

    def check_workspace_runtime_health(self, *, workspace_id: str) -> WorkspaceBringUpResult:
        """Inspect + ``ProbeRunner.check_workspace_health`` only (no start/stop/topology writes)."""
        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceBringUpError("workspace_id is empty")

        _parse_topology_workspace_id(wid)
        container_ref = _sanitize_container_name(wid)

        try:
            ins = self._runtime_adapter.inspect_container(container_id=container_ref)
        except Exception as e:
            raise WorkspaceBringUpError(f"inspect_container failed: {e}") from e

        nid = self._node_id
        tid = str(self._topology_id)

        if not ins.exists:
            return WorkspaceBringUpResult(
                workspace_id=wid,
                success=False,
                node_id=nid,
                topology_id=tid,
                container_id=None,
                container_state="missing",
                probe_healthy=False,
                issues=_issues_or_none(["health:container:not_found"]),
            )

        cid = (ins.container_id or container_ref).strip()
        if not cid:
            return WorkspaceBringUpResult(
                workspace_id=wid,
                success=False,
                node_id=nid,
                topology_id=tid,
                container_id=None,
                container_state=ins.container_state,
                probe_healthy=False,
                issues=_issues_or_none(["health:container:container_id_missing"]),
            )

        state = (ins.container_state or "").strip().lower()
        if state != "running":
            return WorkspaceBringUpResult(
                workspace_id=wid,
                success=False,
                node_id=nid,
                topology_id=tid,
                container_id=cid,
                container_state=ins.container_state,
                probe_healthy=False,
                issues=_issues_or_none([f"health:container:not_running:{state or 'unknown'}"]),
            )

        try:
            health = self._probe_runner.check_workspace_health(
                workspace_id=wid,
                topology_id=tid,
                node_id=nid,
                container_id=cid,
                expected_port=WORKSPACE_IDE_CONTAINER_PORT,
                timeout_seconds=5.0,
            )
        except Exception as e:
            raise WorkspaceBringUpError(f"probe health check failed: {e}") from e

        issue_msgs: list[str] = (
            [f"{i.component}:{i.code}:{i.message}" for i in health.issues] if health.issues else []
        )

        netns_ref: str | None = None
        try:
            netns_ref = self._runtime_adapter.get_container_netns_ref(container_id=cid).netns_ref
        except Exception:
            pass

        return WorkspaceBringUpResult(
            workspace_id=wid,
            success=bool(health.healthy),
            node_id=nid,
            topology_id=tid,
            container_id=cid,
            container_state=health.container_state or ins.container_state,
            netns_ref=netns_ref,
            workspace_ip=health.workspace_ip,
            internal_endpoint=health.internal_endpoint,
            probe_healthy=health.healthy,
            issues=_issues_or_none(issue_msgs),
        )

    def _workspace_project_path_for_snapshot(self, workspace_id: str) -> str:
        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceSnapshotError("workspace_id is empty")
        _parse_topology_workspace_id(wid)
        try:
            return self._ensure_workspace_project_dir(self._workspace_projects_base, wid)
        except ValueError as e:
            raise WorkspaceSnapshotError(str(e)) from e

    @staticmethod
    def _safe_snapshot_tar_extract(tf: tarfile.TarFile, dest: Path) -> None:
        """Reject member paths that escape ``dest`` (tar slip). Symlinks in archives are a TODO for V1."""
        dest_resolved = dest.resolve()
        dest_resolved.mkdir(parents=True, exist_ok=True)
        for m in tf.getmembers():
            name = (m.name or "").strip()
            if not name or name.startswith("/"):
                raise WorkspaceSnapshotError(f"snapshot:import:unsafe_path:{name!r}")
            target = (dest_resolved / name).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise WorkspaceSnapshotError(f"snapshot:import:unsafe_path:{name!r}")
        if sys.version_info >= (3, 12):
            tf.extractall(path=str(dest_resolved), filter="data")
        else:
            tf.extractall(path=str(dest_resolved))

    def export_workspace_filesystem_snapshot(
        self,
        *,
        workspace_id: str,
        archive_path: str,
    ) -> WorkspaceSnapshotOperationResult:
        wid = (workspace_id or "").strip()
        try:
            root = self._workspace_project_path_for_snapshot(wid)
        except WorkspaceSnapshotError as e:
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid or (workspace_id or ""),
                success=False,
                issues=[str(e)],
            )

        dest = Path(archive_path).expanduser().resolve()
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=[f"snapshot:export:mkdir_failed:{e}"],
            )

        root_path = Path(root).resolve()
        if not root_path.is_dir():
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=["snapshot:export:project_dir_missing"],
            )

        try:
            with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT) as tf:
                for path in sorted(root_path.rglob("*")):
                    if not path.is_file():
                        continue
                    try:
                        arcname = path.relative_to(root_path).as_posix()
                    except ValueError:
                        continue
                    tf.add(path, arcname=arcname, recursive=False)
        except OSError as e:
            try:
                if dest.is_file():
                    dest.unlink()
            except OSError:
                pass
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=[f"snapshot:export:io_error:{e}"],
            )

        try:
            size_bytes = int(dest.stat().st_size)
        except OSError:
            size_bytes = None

        log_event(
            logger,
            LogEvent.ORCHESTRATOR_SNAPSHOT_EXPORT_SUCCEEDED,
            workspace_id=wid,
            size_bytes=size_bytes,
        )
        return WorkspaceSnapshotOperationResult(
            workspace_id=wid,
            success=True,
            size_bytes=size_bytes,
            issues=None,
        )

    def import_workspace_filesystem_snapshot(
        self,
        *,
        workspace_id: str,
        archive_path: str,
    ) -> WorkspaceSnapshotOperationResult:
        wid = (workspace_id or "").strip()
        try:
            dest_root = Path(self._workspace_project_path_for_snapshot(wid)).resolve()
        except WorkspaceSnapshotError as e:
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid or (workspace_id or ""),
                success=False,
                issues=[str(e)],
            )

        src = Path(archive_path).expanduser().resolve()
        if not src.is_file():
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=["snapshot:import:archive_missing"],
            )

        try:
            with tarfile.open(src, "r:*") as tf:
                self._safe_snapshot_tar_extract(tf, dest_root)
        except (OSError, tarfile.TarError, WorkspaceSnapshotError) as e:
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=[f"snapshot:import:failed:{e}"],
            )

        log_event(
            logger,
            LogEvent.ORCHESTRATOR_SNAPSHOT_IMPORT_SUCCEEDED,
            workspace_id=wid,
        )
        return WorkspaceSnapshotOperationResult(
            workspace_id=wid,
            success=True,
            size_bytes=int(src.stat().st_size),
            issues=None,
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
