"""Default orchestrator: coordinates ``RuntimeAdapter``, ``TopologyAdapter``, and ``ProbeRunner``.

Mutating flows: bring-up, stop, delete, restart, update (noop or restart-based). Read-only:
``check_workspace_runtime_health``. Placement (``topology_id``, ``node_id``, project base) is
injected until scheduler / ``Workspace_runtime`` persistence exist.

**Persistence boundary:** This package does not write ``Workspace`` / ``WorkspaceRuntime`` / ``WorkspaceJob``
rows. Callers (typically :mod:`app.workers.workspace_job_worker.worker`) persist orchestration outcomes.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import sys
import time
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.libs.observability import metrics as devnest_metrics
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.errors import RuntimeAdapterError
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import (
    CODE_SERVER_CONFIG_CONTAINER_PATH,
    CODE_SERVER_DATA_CONTAINER_PATH,
    WORKSPACE_IDE_CONTAINER_PORT,
    ContainerInspectionResult,
    EnsureRunningRuntimeResult,
    NetnsRefResult,
    WorkspaceExtraBindMountSpec,
)
from app.libs.runtime.runtime_orchestrator import ensure_running_runtime_only
from app.libs.topology.errors import TopologyDeleteError, TopologyError
from app.libs.topology.system.attachment_ops import assert_netns_attach_target_visible
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.results import AttachWorkspaceResult, TopologyJanitorResult

from app.services.node_execution_service.workspace_project_dir import (
    chown_tree_for_workspace_runtime,
    default_local_ensure_workspace_project_dir,
    ensure_code_server_bind_auth_proxy_config,
    stat_mode_octal,
    stat_uid_gid,
    verify_workspace_runtime_can_write_dir,
    verify_workspace_runtime_owns_path,
    workspace_container_uid_gid,
)
from app.services.placement_service.runtime_policy import authoritative_container_ref_required

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
    project_storage_key: str | None
    launch_mode: str
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

    IP lease release failures add ``topology:ip_release_failed:`` and fail the roll-up when present.
    """
    detach_explicit_failure = topology_detached is False and any(
        str(i).strip().startswith("topology:detach_failed:")
        for i in issues
    )
    if detach_explicit_failure:
        return False
    if any(str(i).strip().startswith("topology:ip_release_failed:") for i in issues):
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

    def run_topology_janitor(self, *, stale_attaching_seconds: int = 600) -> TopologyJanitorResult:
        return self._topology_service.run_topology_janitor(
            topology_id=int(self._topology_id),
            node_id=self._node_id,
            stale_attaching_seconds=stale_attaching_seconds,
        )

    def _bring_up_build_context(
        self,
        workspace_id: str,
        project_storage_key: str | None,
        requested_config_version: int | None,
        launch_mode: str | None,
    ) -> _BringUpContext:
        # TODO: reconcile with persisted Workspace_runtime row; container label is the V1 source of truth.
        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceBringUpError("workspace_id is empty")

        ws_int = _parse_topology_workspace_id(wid)
        name = _sanitize_container_name(wid)
        try:
            workspace_host_path = self._ensure_workspace_project_dir(
                self._workspace_projects_base,
                wid,
                project_storage_key,
            )
        except ValueError as e:
            raise WorkspaceBringUpError(str(e)) from e

        labels: dict[str, str] = {
            "devnest.workspace_id": wid,
            "devnest.managed_by": "orchestrator",
        }
        if requested_config_version is not None:
            labels[_CONFIG_VERSION_LABEL] = str(int(requested_config_version))

        logger.info(
            "orchestrator_workspace_project_path_selected",
            extra={
                "workspace_id": wid,
                "project_storage_key": (project_storage_key or "").strip() or None,
                "workspace_host_path": workspace_host_path,
                "launch_mode": (launch_mode or "").strip() or "resume",
                "is_resumed_workspace": ((launch_mode or "").strip().lower() != "new"),
            },
        )

        return _BringUpContext(
            wid=wid,
            ws_int=ws_int,
            container_name=name,
            workspace_host_path=workspace_host_path,
            project_storage_key=(project_storage_key or "").strip() or None,
            launch_mode=(launch_mode or "").strip().lower() or "resume",
            labels=labels,
        )

    @staticmethod
    def _code_server_env(port: int = WORKSPACE_IDE_CONTAINER_PORT) -> dict[str, str]:
        """Return canonical code-server environment variables for the workspace container.

        code-server reads these at startup:
        - ``CS_DISABLE_GETTING_STARTED_OVERRIDE``: suppress the welcome page.
        - ``CODE_SERVER_AUTH``: "none" means no password (auth handled by DevNest gateway sessions).
        - ``PORT``: in-container listen port (must match ``WORKSPACE_IDE_CONTAINER_PORT``).

        Bind-mounted ``config.yaml`` is seeded/patched by :func:`ensure_code_server_bind_auth_proxy_config`
        so persisted ``auth: password`` and missing ``trusted-origins`` cannot override this contract.
        """
        return {
            "CS_DISABLE_GETTING_STARTED_OVERRIDE": "1",
            "CODE_SERVER_AUTH": "none",
            "PORT": str(port),
        }

    def _code_server_extra_bind_mounts(
        self,
        wid: str,
        workspace_host_path: str | None = None,
        launch_mode: str = "resume",
    ) -> list[WorkspaceExtraBindMountSpec]:
        """Build code-server persistence bind mounts for config and data.

        Each workspace gets per-workspace subdirectories under ``workspace_projects_base``
        so that config and extension state persist across stop/start/restart. These are
        in addition to the primary project mount (``/home/coder/project``).

        Returns an empty list when the projects base is not set or the paths cannot be
        constructed (non-blocking; the workspace will still start without persistence for
        those dirs).
        """
        wid_clean = (wid or "").strip()
        if not wid_clean:
            return []
        if workspace_host_path and str(workspace_host_path).strip():
            project_root = os.path.realpath(os.path.expanduser((workspace_host_path or "").strip()))
        else:
            try:
                project_root = self._ensure_workspace_project_dir(self._workspace_projects_base, wid_clean, None)
            except ValueError:
                return []
        if not project_root:
            return []
        cs_base = os.path.join(project_root, "code-server")
        cfg_host = os.path.join(cs_base, "config")
        data_host = os.path.join(cs_base, "data")
        cfg_existed_before = os.path.isdir(cfg_host)
        data_existed_before = os.path.isdir(data_host)
        uid, gid = workspace_container_uid_gid()
        try:
            try:
                euid = os.geteuid()
            except AttributeError:
                euid = -1
            _strict_chown = euid == 0
            logger.info(
                "workspace_code_server_host_prepare_start",
                extra={
                    "workspace_id": wid_clean,
                    "workspace_host_path": project_root,
                    "cs_base": cs_base,
                    "cfg_host": cfg_host,
                    "data_host": data_host,
                    "target_uid": uid,
                    "target_gid": gid,
                    "control_plane_euid": euid,
                },
            )
            os.makedirs(cfg_host, exist_ok=True)
            os.makedirs(data_host, exist_ok=True)
            cfg_host = os.path.realpath(cfg_host)
            data_host = os.path.realpath(data_host)
            state_reset = self._reset_code_server_state_for_new_workspace(
                workspace_id=wid_clean,
                data_host=data_host,
                launch_mode=launch_mode,
            )
            # Seed ``config.yaml`` before chown so the workspace user can read it (auth/proxy contract).
            ensure_code_server_bind_auth_proxy_config(cfg_host)
            # Chown the whole ``code-server`` tree (``chown -R`` as root; Python walk fallback).
            chown_tree_for_workspace_runtime(cs_base, strict=_strict_chown)
            for label, host in (("config", cfg_host), ("data", data_host)):
                verify_workspace_runtime_owns_path(host)
                verify_workspace_runtime_can_write_dir(host)
                su, sg = stat_uid_gid(host)
                logger.info(
                    "workspace_code_server_host_prepare_ok",
                    extra={
                        "workspace_id": wid_clean,
                        "role": label,
                        "host_path": host,
                        "launch_mode": launch_mode,
                        "directory_preexisted": data_existed_before if label == "data" else cfg_existed_before,
                        "state_reset_for_new_workspace": state_reset if label == "data" else False,
                        "stat_uid": su,
                        "stat_gid": sg,
                        "target_uid": uid,
                        "target_gid": gid,
                        "mode_oct": stat_mode_octal(host),
                        "chown_performed_under_cs_base": True,
                        "chown_strict": _strict_chown,
                        "pre_start_writability_ok": True,
                        "writability_checked_with_privdrop": bool(
                            (shutil.which("setpriv") or shutil.which("runuser")) and _strict_chown,
                        ),
                    },
                )
        except OSError as e:
            logger.warning(
                "orchestrator_code_server_bind_mount_mkdir_failed",
                extra={
                    "workspace_id": wid,
                    "error": str(e),
                    "cfg_host": cfg_host,
                    "data_host": data_host,
                    "target_uid": uid,
                    "target_gid": gid,
                },
            )
            raise WorkspaceBringUpError(
                "workspace host bind-mount path not writable by runtime user "
                f"(prepare/verify failed for code-server dirs under {cs_base!r}): {e}. "
                "Ensure WORKSPACE_PROJECTS_BASE is on the Docker host filesystem, the control plane "
                "runs as root there so chown to the workspace UID/GID succeeds, or pre-chown these paths.",
            ) from e
        return [
            WorkspaceExtraBindMountSpec(
                host_path=cfg_host,
                container_path=CODE_SERVER_CONFIG_CONTAINER_PATH,
            ),
            WorkspaceExtraBindMountSpec(
                host_path=data_host,
                container_path=CODE_SERVER_DATA_CONTAINER_PATH,
            ),
        ]

    @staticmethod
    def _reset_code_server_state_for_new_workspace(
        *,
        workspace_id: str,
        data_host: str,
        launch_mode: str,
    ) -> bool:
        """
        For newly created workspaces, clear persisted code-server UI/session state before start.

        This keeps a surprising stale bind mount from reopening unrelated tabs/files while
        preserving ``extensions/`` if present. Resume flows keep the directory intact.
        """
        mode = (launch_mode or "").strip().lower() or "resume"
        if mode != "new":
            logger.info(
                "workspace_code_server_state_reuse",
                extra={
                    "workspace_id": workspace_id,
                    "data_host": data_host,
                    "launch_mode": mode,
                    "state_reset": False,
                },
            )
            return False
        if not os.path.isdir(data_host):
            return False
        removed_any = False
        for entry in os.listdir(data_host):
            if entry == "extensions":
                continue
            target = os.path.join(data_host, entry)
            try:
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target, ignore_errors=False)
                else:
                    os.unlink(target)
                removed_any = True
            except FileNotFoundError:
                continue
        logger.info(
            "workspace_code_server_state_reset",
            extra={
                "workspace_id": workspace_id,
                "data_host": data_host,
                "launch_mode": mode,
                "state_reset": removed_any,
            },
        )
        return removed_any

    def _bring_up_start_container(
        self,
        ctx: _BringUpContext,
        *,
        cpu_limit_cores: float | None = None,
        memory_limit_mib: int | None = None,
        env: dict | None = None,
    ) -> EnsureRunningRuntimeResult:
        """Run ``ensure_running_runtime_only`` (ensure → start → inspect → netns unless skip-linux-attach).

        Injects code-server environment variables and sets up persistence bind mounts for
        the code-server config and data directories (in addition to the primary project mount).
        """
        memory_limit_bytes: int | None = None
        if memory_limit_mib is not None and memory_limit_mib > 0:
            memory_limit_bytes = int(memory_limit_mib) * 1024 * 1024

        # Merge code-server defaults with caller-provided env (caller values win).
        merged_env: dict[str, str] = {**self._code_server_env(), **(env or {})}

        # Add code-server persistence bind mounts.
        cs_extra_mounts = self._code_server_extra_bind_mounts(
            ctx.wid,
            ctx.workspace_host_path,
            ctx.launch_mode,
        )
        for spec in cs_extra_mounts or []:
            hp = (spec.host_path or "").strip()
            if not hp:
                continue
            try:
                verify_workspace_runtime_owns_path(hp)
                verify_workspace_runtime_can_write_dir(hp)
            except OSError as e:
                raise WorkspaceBringUpError(
                    "workspace host path not writable by runtime user "
                    f"(pre-container final check failed for bind source {hp!r} → {spec.container_path!r}): {e}",
                ) from e

        proj = (ctx.workspace_host_path or "").strip()
        if proj:
            try:
                verify_workspace_runtime_owns_path(proj)
                verify_workspace_runtime_can_write_dir(proj)
                logger.info(
                    "workspace_project_host_pre_start_ok",
                    extra={
                        "workspace_id": ctx.wid,
                        "host_path": proj,
                        "stat_uid": stat_uid_gid(proj)[0],
                        "stat_gid": stat_uid_gid(proj)[1],
                        "mode_oct": stat_mode_octal(proj),
                    },
                )
            except OSError as e:
                raise WorkspaceBringUpError(
                    "workspace host path not writable by runtime user "
                    f"(pre-container final check failed for project bind {proj!r}): {e}",
                ) from e

        try:
            return ensure_running_runtime_only(
                self._runtime_adapter,
                name=ctx.container_name,
                image=self._workspace_image,
                ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
                labels=ctx.labels,
                workspace_host_path=ctx.workspace_host_path,
                skip_netns_resolution=_env_skip_linux_topology_attachment(),
                cpu_limit=cpu_limit_cores,
                memory_limit_bytes=memory_limit_bytes,
                env=merged_env,
                extra_bind_mounts=cs_extra_mounts if cs_extra_mounts else None,
            )
        except RuntimeAdapterError as e:
            raise WorkspaceBringUpError(f"runtime bring-up failed: {e}") from e

    def _log_workspace_runtime_attach_snapshot(
        self,
        ctx: _BringUpContext,
        *,
        phase: str,
        ins: ContainerInspectionResult,
    ) -> None:
        proc_visible: bool | None = None
        if sys.platform == "linux" and ins.pid is not None and ins.pid > 0:
            proc_visible = os.path.isdir(f"/proc/{ins.pid}")
        logger.info(
            "workspace_runtime_attach_boundary",
            extra={
                "workspace_id": ctx.wid,
                "phase": phase,
                "container_id": ins.container_id,
                "exists": ins.exists,
                "container_state": ins.container_state,
                "pid": ins.pid,
                "started_at": ins.started_at,
                "finished_at": ins.finished_at,
                "exit_code": ins.exit_code,
                "proc_pid_visible_control_plane": proc_visible,
            },
        )

    def _bring_up_wait_workspace_alive_for_topology(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult,
        *,
        phase: str,
        max_wait_s: float,
    ) -> ContainerInspectionResult:
        """
        Poll inspect until the workspace is running with a host PID (or fail with log tail).

        Surfaces ``workspace runtime exited before topology attach`` before ``ip link set … netns``,
        so invalid netns errors are not the first surfaced failure when the runtime is already dead.

        This wait intentionally does **not** require ``/proc/<pid>`` to exist: unit tests and some
        split-brain setups use non-local inspect PIDs. Linux ``/proc`` visibility for attach is
        enforced separately via ``assert_netns_attach_target_visible`` before ``attach_workspace``.
        """
        if _env_skip_linux_topology_attachment():
            ins = self._runtime_adapter.inspect_container(container_id=running.container_id)
            self._log_workspace_runtime_attach_snapshot(ctx, phase=phase, ins=ins)
            return ins

        deadline = time.monotonic() + max(0.05, float(max_wait_s))
        interval_s = 0.12
        last: ContainerInspectionResult | None = None
        while True:
            ins = self._runtime_adapter.inspect_container(container_id=running.container_id)
            last = ins
            self._log_workspace_runtime_attach_snapshot(ctx, phase=phase, ins=ins)
            if not ins.exists:
                tail = self._runtime_adapter.fetch_container_log_tail(
                    container_id=running.container_id,
                    lines=120,
                )
                raise WorkspaceBringUpError(
                    "workspace runtime exited before topology attach "
                    f"(phase={phase!r}, container missing). "
                    "If logs are empty, compensating rollback may have removed the container after an earlier failure.\n"
                    f"docker log tail:\n{tail or '(empty)'}",
                )
            if ins.container_state in ("exited", "dead"):
                tail = self._runtime_adapter.fetch_container_log_tail(
                    container_id=running.container_id,
                    lines=120,
                )
                raise WorkspaceBringUpError(
                    "workspace runtime exited before topology attach "
                    f"(phase={phase!r}, state={ins.container_state!r}, pid={ins.pid!r}, "
                    f"started_at={ins.started_at!r}, finished_at={ins.finished_at!r}, exit_code={ins.exit_code!r}). "
                    "Note: exit 143 often follows SIGTERM from bring-up rollback after a *different* failure; "
                    "check this message and log tail for the first error, not only post-rollback inspect.\n"
                    f"docker log tail:\n{tail or '(empty)'}",
                )
            if ins.container_state == "running" and ins.pid is not None and ins.pid > 0:
                return ins
            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(interval_s, max(0.01, deadline - now)))

        ins = last
        assert ins is not None
        tail = self._runtime_adapter.fetch_container_log_tail(
            container_id=running.container_id,
            lines=120,
        )
        proc_vis: bool | None = None
        if sys.platform == "linux" and ins.pid is not None and ins.pid > 0:
            proc_vis = os.path.isdir(f"/proc/{ins.pid}")
        raise WorkspaceBringUpError(
            "workspace runtime exited before topology attach "
            f"(phase={phase!r}, waited {max_wait_s}s: state={ins.container_state!r}, pid={ins.pid!r}, "
            f"started_at={ins.started_at!r}, finished_at={ins.finished_at!r}, exit_code={ins.exit_code!r}, "
            f"proc_pid_visible_control_plane={proc_vis!r}).\n"
            f"docker log tail:\n{tail or '(empty)'}",
        )

    def _bring_up_attach_topology(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult,
    ) -> tuple[NetnsRefResult, AttachWorkspaceResult]:
        """Ensure node topology, allocate IP, attach workspace veth to bridge."""
        try:
            self._bring_up_wait_workspace_alive_for_topology(
                ctx,
                running,
                phase="before_ensure_node_topology",
                max_wait_s=2.5,
            )
            self._topology_service.ensure_node_topology(
                topology_id=self._topology_id,
                node_id=self._node_id,
            )
            ip_res = self._topology_service.allocate_workspace_ip(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ctx.ws_int,
            )
            self._bring_up_wait_workspace_alive_for_topology(
                ctx,
                running,
                phase="after_allocate_workspace_ip_before_netns",
                max_wait_s=12.0,
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
                self._bring_up_wait_workspace_alive_for_topology(
                    ctx,
                    running,
                    phase="before_get_container_netns_ref",
                    max_wait_s=3.0,
                )
                netns = self._runtime_adapter.get_container_netns_ref(container_id=running.container_id)
                # ``assert_netns_attach_target_visible`` requires ``/proc/<pid>`` in *this* PID namespace.
                # Unit tests use fake inspect PIDs; real attach still validates in ``DbTopologyAdapter._run_linux_attach``.
                precheck_netns = True
                if sys.platform == "linux" and netns.pid > 0:
                    precheck_netns = os.path.isdir(f"/proc/{netns.pid}")
                if precheck_netns:
                    try:
                        assert_netns_attach_target_visible(netns.netns_ref)
                    except RuntimeError as e:
                        tail = self._runtime_adapter.fetch_container_log_tail(
                            container_id=running.container_id,
                            lines=160,
                        )
                        raise WorkspaceBringUpError(
                            "workspace runtime exited before topology attach "
                            f"(netns target not visible from control plane before linux attach: {e}).\n"
                            f"docker log tail:\n{tail or '(empty)'}",
                        ) from e
                else:
                    logger.debug(
                        "workspace_runtime_netns_precheck_skipped_host_proc_not_visible",
                        extra={
                            "workspace_id": ctx.wid,
                            "container_id": running.container_id,
                            "pid": netns.pid,
                        },
                    )
            attach_res = self._topology_service.attach_workspace(
                topology_id=self._topology_id,
                node_id=self._node_id,
                workspace_id=ctx.ws_int,
                container_id=running.container_id,
                netns_ref=netns.netns_ref,
                workspace_ip=ip_res.workspace_ip,
            )
        except TopologyError as e:
            ins = self._runtime_adapter.inspect_container(container_id=running.container_id)
            tail = (self._runtime_adapter.fetch_container_log_tail(
                container_id=running.container_id,
                lines=200,
            ) or "").strip()
            dead = (
                not ins.exists
                or ins.container_state in ("exited", "dead")
                or ins.pid is None
                or ins.pid <= 0
            )
            if dead:
                raise WorkspaceBringUpError(
                    "workspace runtime exited before topology attach "
                    f"(inspect: state={ins.container_state!r}, pid={ins.pid!r}, "
                    f"started_at={ins.started_at!r}, finished_at={ins.finished_at!r}, exit_code={ins.exit_code!r}). "
                    f"Downstream topology error (often a symptom when init PID is gone): {e}\n"
                    f"docker log tail:\n{tail[-8000:] if tail else '(empty)'}",
                ) from e
            raise WorkspaceBringUpError(
                f"topology bring-up failed (after workspace runtime was running): {e}",
            ) from e
        except RuntimeAdapterError as e:
            raise WorkspaceBringUpError(
                f"workspace runtime not ready for topology (PID/netns handoff): {e}",
            ) from e
        return netns, attach_res

    def _bring_up_run_probe(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult,
        netns: NetnsRefResult,
        attach_res: AttachWorkspaceResult,
    ) -> WorkspaceBringUpResult:
        # Route registration: workspace job worker calls route-admin after RUNNING (not orchestrator).
        from app.libs.common.config import get_settings  # noqa: PLC0415

        cfg = get_settings()
        wait_total = float(cfg.devnest_workspace_bringup_ide_tcp_wait_seconds)
        poll_interval = float(cfg.devnest_workspace_bringup_ide_tcp_poll_interval_seconds)
        if not math.isfinite(wait_total):
            wait_total = 90.0
        if not math.isfinite(poll_interval):
            poll_interval = 1.5
        wait_total = max(1.0, min(600.0, wait_total))
        poll_interval = max(0.05, min(30.0, poll_interval))

        ws_ip = (attach_res.workspace_ip or "").strip()
        tcp_reached = False
        try:
            if ws_ip:
                deadline = time.monotonic() + wait_total
                while time.monotonic() < deadline:
                    remaining = max(0.5, deadline - time.monotonic())
                    per_try = min(5.0, remaining)
                    last_tcp = self._probe_runner.check_service_reachable(
                        workspace_ip=ws_ip,
                        port=WORKSPACE_IDE_CONTAINER_PORT,
                        timeout_seconds=per_try,
                    )
                    if last_tcp.healthy:
                        tcp_reached = True
                        break
                    time.sleep(poll_interval)

            # After attach, code-server may need many seconds before the IDE port accepts TCP; once it
            # does, allow a proportionally larger window for HTTP readiness on slow disks.
            if ws_ip and tcp_reached:
                final_timeout = min(45.0, max(8.0, wait_total / 6.0))
            else:
                final_timeout = 5.0

            health = self._probe_runner.check_workspace_health(
                workspace_id=ctx.wid,
                topology_id=str(self._topology_id),
                node_id=self._node_id,
                container_id=running.container_id,
                expected_port=WORKSPACE_IDE_CONTAINER_PORT,
                timeout_seconds=final_timeout,
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

    def _bring_up_compensating_rollback(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult | None,
        *,
        reason: str,
    ) -> tuple[bool, list[str], WorkspaceStopResult | None]:
        """Detach, stop engine, release IP lease (idempotent).

        Returns ``(ok, issue strings, last stop result)`` where ``last stop result`` is set when
        ``stop_workspace_runtime`` returned ``success=True`` on the final attempt. Never raises.
        """
        log_event(
            logger,
            LogEvent.ORCHESTRATOR_BRINGUP_ROLLBACK,
            level=logging.WARNING,
            workspace_id=ctx.wid,
            reason=reason[:200],
            topology_id=self._topology_id,
            node_id=self._node_id,
        )
        metric_reason = "probe_unhealthy" if reason == "probe_unhealthy" else "exception"
        devnest_metrics.record_bringup_rollback(reason=metric_reason)
        cid = (running.container_id if running is not None else None) or None
        if cid is not None:
            cid = str(cid).strip() or None
        issues: list[str] = []
        last_stop: WorkspaceStopResult | None = None
        logger.warning(
            "orchestrator_bringup_rollback_docker_stop_pending",
            extra={
                "workspace_id": ctx.wid,
                "container_id": cid,
                "rollback_sends_sigterm_first": True,
                "note": "docker stop defaults to SIGTERM then SIGKILL after stop timeout; exit 143 is normal here",
            },
        )
        for attempt in (1, 2):
            try:
                stop_out = self.stop_workspace_runtime(
                    workspace_id=ctx.wid,
                    container_id=cid,
                    requested_by=None,
                    release_ip_lease=True,
                )
                last_stop = stop_out
                if stop_out.success:
                    return True, issues, stop_out
                issues.append(
                    f"rollback:stop_incomplete:{attempt}:"
                    f"{','.join(stop_out.issues or ['no_issues_field'])}",
                )
            except WorkspaceStopError as e:
                issues.append(f"rollback:stop_error:{attempt}:{e}")
            if attempt == 1:
                time.sleep(0.35)
        logger.warning(
            "orchestrator_bringup_rollback_stop_failed",
            extra={"workspace_id": ctx.wid, "issues": issues[:5]},
        )
        devnest_metrics.record_bringup_rollback_failed()
        return False, issues, last_stop

    def _bring_up_log_runtime_failure_before_rollback(
        self,
        ctx: _BringUpContext,
        running: EnsureRunningRuntimeResult | None,
        *,
        failure_kind: str,
        exc: BaseException,
    ) -> None:
        """Fresh inspect + log tail so the first workspace error survives truncated job logs."""
        if running is None or not str(running.container_id).strip():
            logger.warning(
                "workspace_runtime_bringup_failure_evidence_skipped",
                extra={
                    "workspace_id": ctx.wid,
                    "failure_kind": failure_kind,
                    "reason": "no_container_id",
                    "exc_type": type(exc).__name__,
                    "exc_head": str(exc)[:2000],
                },
            )
            return
        cid = str(running.container_id).strip()
        try:
            ins = self._runtime_adapter.inspect_container(container_id=cid)
            tail = (self._runtime_adapter.fetch_container_log_tail(container_id=cid, lines=250) or "").strip()
        except Exception as gather_e:
            logger.warning(
                "workspace_runtime_bringup_failure_evidence_gather_error",
                extra={
                    "workspace_id": ctx.wid,
                    "container_id": cid,
                    "error": str(gather_e)[:800],
                },
            )
            return
        logger.warning(
            "workspace_runtime_bringup_failure_evidence",
            extra={
                "workspace_id": ctx.wid,
                "failure_kind": failure_kind,
                "exc_type": type(exc).__name__,
                "exc_message_head": str(exc)[:2000],
                "container_id": ins.container_id,
                "inspect_state": ins.container_state,
                "inspect_pid": ins.pid,
                "inspect_started_at": ins.started_at,
                "inspect_finished_at": ins.finished_at,
                "inspect_exit_code": ins.exit_code,
                "log_tail_len": len(tail),
            },
        )
        if tail:
            logger.warning(
                "workspace_runtime_bringup_failure_log_tail",
                extra={"workspace_id": ctx.wid, "container_id": cid, "tail": tail[-12000:]},
            )

    def bring_up_workspace_runtime(
        self,
        *,
        workspace_id: str,
        project_storage_key: str | None = None,
        requested_config_version: int | None = None,
        cpu_limit_cores: float | None = None,
        memory_limit_mib: int | None = None,
        env: dict | None = None,
        features: dict | None = None,
        launch_mode: str | None = None,
    ) -> WorkspaceBringUpResult:
        """
        Start workspace container, wire topology attachment, run service probe.

        ``cpu_limit_cores`` and ``memory_limit_mib`` are applied to the container when non-None.
        ``env`` is merged into the container environment.
        ``features`` carries optional feature flags (reserved; currently informational).

        Returns a :class:`WorkspaceBringUpResult` for the worker to persist on ``WorkspaceRuntime``.
        """
        ctx = self._bring_up_build_context(
            workspace_id,
            project_storage_key,
            requested_config_version,
            launch_mode,
        )
        log_event(
            logger,
            LogEvent.ORCHESTRATOR_BRINGUP_STARTED,
            workspace_id=ctx.wid,
            requested_config_version=requested_config_version,
            topology_id=self._topology_id,
            node_id=self._node_id,
            cpu_limit_cores=cpu_limit_cores,
            memory_limit_mib=memory_limit_mib,
        )
        running: EnsureRunningRuntimeResult | None = None
        try:
            running = self._bring_up_start_container(
                ctx,
                cpu_limit_cores=cpu_limit_cores,
                memory_limit_mib=memory_limit_mib,
                env=env,
            )
            logger.info(
                "orchestrator_bringup_sequence",
                extra={
                    "workspace_id": ctx.wid,
                    "step": "after_ensure_running_runtime_only",
                    "container_id": running.container_id,
                    "container_state": running.container_state,
                    "pid": running.pid,
                    "next": "attach_topology",
                },
            )
            netns, attach_res = self._bring_up_attach_topology(ctx, running)
            result = self._bring_up_run_probe(ctx, running, netns, attach_res)
        except WorkspaceBringUpError as e:
            self._bring_up_log_runtime_failure_before_rollback(
                ctx,
                running,
                failure_kind="WorkspaceBringUpError",
                exc=e,
            )
            log_event(
                logger,
                LogEvent.ORCHESTRATOR_BRINGUP_FAILED,
                level=logging.WARNING,
                workspace_id=ctx.wid,
                error=str(e)[:500],
            )
            rb_ok, rb_issues, stop_out = self._bring_up_compensating_rollback(ctx, running, reason="exception")
            rid = (stop_out.container_id if stop_out else None) or (
                running.container_id if running is not None else None
            )
            rst = stop_out.container_state if stop_out else None
            raise WorkspaceBringUpError(
                str(e),
                rollback_attempted=True,
                rollback_succeeded=rb_ok,
                rollback_issues=rb_issues,
                rollback_container_id=rid,
                rollback_container_state=rst,
            ) from e
        except Exception as e:
            self._bring_up_log_runtime_failure_before_rollback(
                ctx,
                running,
                failure_kind=type(e).__name__,
                exc=e,
            )
            log_event(
                logger,
                LogEvent.ORCHESTRATOR_BRINGUP_FAILED,
                level=logging.WARNING,
                workspace_id=ctx.wid,
                error=str(e)[:500],
            )
            rb_ok, rb_issues, stop_out = self._bring_up_compensating_rollback(ctx, running, reason="exception")
            rid = (stop_out.container_id if stop_out else None) or (
                running.container_id if running is not None else None
            )
            rst = stop_out.container_state if stop_out else None
            raise WorkspaceBringUpError(
                f"unexpected bring-up failure: {e}",
                rollback_attempted=True,
                rollback_succeeded=rb_ok,
                rollback_issues=rb_issues,
                rollback_container_id=rid,
                rollback_container_state=rst,
            ) from e
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
            self._bring_up_log_runtime_failure_before_rollback(
                ctx,
                running,
                failure_kind="probe_unhealthy",
                exc=RuntimeError("; ".join(result.issues or ["probe_unhealthy"])),
            )
            rb_ok, rb_issues, stop_out = self._bring_up_compensating_rollback(ctx, running, reason="probe_unhealthy")
            merged_issues = list(result.issues or [])
            merged_issues.extend(rb_issues)
            cid = (stop_out.container_id if stop_out else None) or result.container_id
            cst = (stop_out.container_state if stop_out else None) or result.container_state
            result = WorkspaceBringUpResult(
                workspace_id=result.workspace_id,
                success=False,
                node_id=result.node_id,
                topology_id=result.topology_id,
                container_id=cid,
                container_state=cst,
                netns_ref=result.netns_ref,
                workspace_ip=None if rb_ok else result.workspace_ip,
                internal_endpoint=None if rb_ok else result.internal_endpoint,
                probe_healthy=False,
                issues=_issues_or_none(merged_issues),
                rollback_attempted=True,
                rollback_succeeded=rb_ok,
                rollback_issues=_issues_or_none(rb_issues),
            )
        return result

    def _stop_load_inspection(
        self,
        wid: str,
        container_ref: str,
    ) -> tuple[int, str | None, str | None]:
        """Inspect container using ``container_ref`` (persisted engine id or dev-only deterministic name)."""
        ws_int = _parse_topology_workspace_id(wid)
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
        container_id: str | None = None,
        requested_by: str | None = None,
        release_ip_lease: bool = False,
    ) -> WorkspaceStopResult:
        """
        Detach topology (best-effort), stop container (best-effort).

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available. Falls back to deterministic name derivation when ``None``.

        When ``release_ip_lease`` is true, releases the topology IP row after detach/stop (idempotent).

        Returns :class:`WorkspaceStopResult` for the worker to persist (e.g. cleared or stopped runtime).
        """
        _ = requested_by  # TODO: persist audit trail / emit stop event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceStopError("workspace_id is empty")

        cid_in = (container_id or "").strip()
        if authoritative_container_ref_required() and not cid_in:
            return WorkspaceStopResult(
                workspace_id=wid,
                success=False,
                container_id=None,
                container_state=None,
                topology_detached=None,
                issues=["runtime:authoritative_container_id_required"],
            )

        container_ref = cid_in or _sanitize_container_name(wid)
        logger.info(
            "orchestrator_stop_start",
            extra={
                "workspace_id": wid,
                "topology_id": self._topology_id,
                "node_id": self._node_id,
                "release_ip_lease": release_ip_lease,
            },
        )

        issues: list[str] = []
        ws_int, container_id, container_state_before = self._stop_load_inspection(wid, container_ref)
        topology_detached = self._stop_detach_topology_best_effort(ws_int, issues)
        stop_success, stopped_state = self._stop_container_best_effort(container_id, issues)

        if release_ip_lease:
            try:
                self._topology_service.release_workspace_ip_lease(
                    topology_id=self._topology_id,
                    node_id=self._node_id,
                    workspace_id=ws_int,
                )
            except TopologyError as e:
                issues.append(f"topology:ip_release_failed:{e}")

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
        container_id: str | None = None,
        requested_by: str | None = None,
    ) -> WorkspaceDeleteResult:
        """
        Detach, delete container, optionally delete node topology runtime.

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available. Falls back to deterministic name derivation when ``None``.

        Returns :class:`WorkspaceDeleteResult` for the worker to clear ``WorkspaceRuntime`` on success.
        """
        _ = requested_by  # TODO: persist audit trail / emit delete event

        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceDeleteError("workspace_id is empty")

        cid_in = (container_id or "").strip()
        if authoritative_container_ref_required() and not cid_in:
            return WorkspaceDeleteResult(
                workspace_id=wid,
                success=False,
                container_deleted=False,
                topology_detached=None,
                topology_deleted=None,
                container_id=None,
                issues=["runtime:authoritative_container_id_required"],
            )

        container_ref = cid_in or _sanitize_container_name(wid)
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
        project_storage_key: str | None = None,
        container_id: str | None = None,
        requested_by: str | None = None,
        requested_config_version: int | None = None,
    ) -> WorkspaceRestartResult:
        """
        Stop then bring-up workspace runtime (optional new ``requested_config_version`` label).

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available; it is used for the stop phase only (the bring-up phase allocates a new ID).

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
            stop_res = self.stop_workspace_runtime(
                workspace_id=wid,
                container_id=container_id,
                requested_by=requested_by,
            )
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
                project_storage_key=project_storage_key,
                requested_config_version=requested_config_version,
                launch_mode="resume",
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
        project_storage_key: str | None = None,
        container_id: str | None = None,
        requested_config_version: int,
        requested_by: str | None = None,
    ) -> WorkspaceUpdateResult:
        """
        If container config label matches ``requested_config_version``, health-check only (noop).

        Otherwise restarts the workspace to apply the new version.

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available.
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

        cid_in = (container_id or "").strip()
        if authoritative_container_ref_required() and not cid_in:
            return WorkspaceUpdateResult(
                workspace_id=wid,
                success=False,
                current_config_version=0,
                requested_config_version=requested_config_version,
                update_strategy="blocked",
                no_op=False,
                issues=_issues_or_none(["runtime:authoritative_container_id_required"]),
            )

        container_ref = cid_in or _sanitize_container_name(wid)
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
                project_storage_key=project_storage_key,
                container_id=container_id,
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

    def check_workspace_runtime_health(
        self,
        *,
        workspace_id: str,
        container_id: str | None = None,
    ) -> WorkspaceBringUpResult:
        """Inspect + ``ProbeRunner.check_workspace_health`` only (no start/stop/topology writes).

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``.
        In staging/production (strict placement), a non-empty engine id is required; deterministic
        name fallback is development-only when env fallback is allowed.
        """
        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceBringUpError("workspace_id is empty")

        _parse_topology_workspace_id(wid)
        cid_in = (container_id or "").strip()
        if authoritative_container_ref_required() and not cid_in:
            return WorkspaceBringUpResult(
                workspace_id=wid,
                success=False,
                node_id=self._node_id,
                topology_id=str(self._topology_id),
                container_id=None,
                probe_healthy=False,
                issues=_issues_or_none(["runtime:authoritative_container_id_required"]),
            )

        container_ref = cid_in or _sanitize_container_name(wid)

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

    def _workspace_project_path_for_snapshot(
        self,
        workspace_id: str,
        project_storage_key: str | None = None,
    ) -> str:
        wid = (workspace_id or "").strip()
        if not wid:
            raise WorkspaceSnapshotError("workspace_id is empty")
        _parse_topology_workspace_id(wid)
        try:
            return self._ensure_workspace_project_dir(
                self._workspace_projects_base,
                wid,
                project_storage_key,
            )
        except ValueError as e:
            raise WorkspaceSnapshotError(str(e)) from e

    @staticmethod
    def _validate_tar_members(tf: tarfile.TarFile, dest_resolved: Path) -> None:
        """Reject any member that would escape ``dest_resolved`` (tar-slip / path traversal).

        Checks applied per member:
        - Absolute paths are rejected.
        - ``..`` components that escape the destination are rejected.
        - Resolved destination must be within ``dest_resolved``.
        - Device / block-special members are rejected.
        - Hard-links that point outside dest are rejected.
        """
        for m in tf.getmembers():
            name = (m.name or "").strip()
            # Reject empty names, absolute paths, or overt traversal sequences
            if not name or name.startswith("/") or name.startswith(".."):
                raise WorkspaceSnapshotError(f"snapshot:import:unsafe_path:{name!r}")
            # Reject device or block-special members
            if m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                raise WorkspaceSnapshotError(f"snapshot:import:unsafe_member_type:{name!r}")
            target = (dest_resolved / name).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise WorkspaceSnapshotError(f"snapshot:import:unsafe_path:{name!r}")
            # Reject hard-links pointing outside dest
            if m.islnk():
                link_target = (dest_resolved / (m.linkname or "")).resolve()
                if dest_resolved not in link_target.parents and link_target != dest_resolved:
                    raise WorkspaceSnapshotError(f"snapshot:import:unsafe_hardlink:{name!r}")

    @staticmethod
    def _safe_snapshot_tar_extract(tf: tarfile.TarFile, dest: Path) -> None:
        """Extract archive to ``dest`` with full path-traversal protection and atomic rename.

        Safety guarantees:
        1. All member paths validated against ``dest`` before extraction begins.
        2. Extraction performed into a sibling temp directory to prevent partial-restore.
        3. On success: destination is atomically swapped (existing renamed to .bak, temp
           renamed to dest, backup removed).
        4. On failure: temp directory is removed and original dest is preserved intact.
        """
        import shutil  # noqa: PLC0415

        dest_resolved = dest.resolve()
        dest_resolved.mkdir(parents=True, exist_ok=True)

        # Step 1: Validate all members before touching the filesystem.
        DefaultOrchestratorService._validate_tar_members(tf, dest_resolved)

        # Step 2: Extract into a sibling temp dir for atomicity.
        parent = dest_resolved.parent
        tmp_dir = Path(tempfile.mkdtemp(dir=parent, prefix=".devnest-restore-tmp-"))
        try:
            if sys.version_info >= (3, 12):
                tf.extractall(path=str(tmp_dir), filter="data")
            else:
                tf.extractall(path=str(tmp_dir))

            # Step 3: Atomic swap — rename current dest to .bak, promote temp, remove .bak.
            bak_dir = dest_resolved.parent / (dest_resolved.name + ".bak")
            try:
                if bak_dir.exists():
                    shutil.rmtree(bak_dir, ignore_errors=True)
                if dest_resolved.exists():
                    dest_resolved.rename(bak_dir)
                tmp_dir.rename(dest_resolved)
                if bak_dir.exists():
                    shutil.rmtree(bak_dir, ignore_errors=True)
            except OSError as e:
                # Rollback: restore backup if rename partially failed.
                if not dest_resolved.exists() and bak_dir.exists():
                    try:
                        bak_dir.rename(dest_resolved)
                    except OSError:
                        pass
                raise WorkspaceSnapshotError(f"snapshot:import:atomic_swap_failed:{e}") from e
        except WorkspaceSnapshotError:
            raise
        except (OSError, tarfile.TarError) as e:
            raise WorkspaceSnapshotError(f"snapshot:import:extract_failed:{e}") from e
        finally:
            # Always clean up the temp dir if it still exists (e.g. exception before rename).
            if tmp_dir.exists():
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

    def export_workspace_filesystem_snapshot(
        self,
        *,
        workspace_id: str,
        project_storage_key: str | None = None,
        archive_path: str,
    ) -> WorkspaceSnapshotOperationResult:
        wid = (workspace_id or "").strip()
        try:
            root = self._workspace_project_path_for_snapshot(wid, project_storage_key)
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
        project_storage_key: str | None = None,
        archive_path: str,
    ) -> WorkspaceSnapshotOperationResult:
        wid = (workspace_id or "").strip()
        try:
            dest_root = Path(self._workspace_project_path_for_snapshot(wid, project_storage_key)).resolve()
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

        # Validate the archive is a valid tar before opening for extraction.
        if not tarfile.is_tarfile(src):
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=["snapshot:import:invalid_archive_format"],
            )

        try:
            with tarfile.open(src, "r:*") as tf:
                self._safe_snapshot_tar_extract(tf, dest_root)
        except WorkspaceSnapshotError as e:
            return WorkspaceSnapshotOperationResult(
                workspace_id=wid,
                success=False,
                issues=[str(e)],
            )
        except (OSError, tarfile.TarError) as e:
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
