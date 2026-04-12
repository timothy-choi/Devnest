"""
Docker ``RuntimeAdapter`` that runs the engine CLI on a remote EC2 host via AWS SSM.

Uses the same inspection normalization as :mod:`app.libs.runtime.docker_runtime` (JSON shape).
Project bind paths must be **absolute on the remote Linux host** (``/var/...``), not the worker.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from app.libs.topology.system.command_runner import CommandRunner

from .docker_runtime import (
    WORKSPACE_IDE_CONTAINER_PORT,
    WORKSPACE_PROJECT_CONTAINER_PATH,
    WorkspaceExtraBindMountSpec,
    WorkspaceProjectMountSpec,
    _container_state_needs_engine_stop,
    _default_stop_timeout_s,
    _extra_bind_strings,
    _inspection_not_found,
    _normalize_inspection,
    _port_bindings_from_spec,
    _resolve_image,
    _resolved_ports_tuple,
)
from .errors import (
    ContainerCreateError,
    ContainerDeleteError,
    ContainerNotFoundError,
    ContainerStartError,
    ContainerStopError,
    NetnsRefError,
)
from .interfaces import RuntimeAdapter
from .models import (
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
)


def _resolve_project_host_path_remote(
    project_mount: WorkspaceProjectMountSpec | None,
    workspace_host_path: str | None,
) -> tuple[str, bool]:
    """Like ``docker_runtime`` project resolution; only POSIX absolute paths (remote host)."""
    pm_raw = (
        str(project_mount.host_path).strip()
        if project_mount is not None and project_mount.host_path is not None
        else ""
    )
    wh_raw = str(workspace_host_path).strip() if workspace_host_path and str(workspace_host_path).strip() else ""
    if pm_raw and wh_raw and pm_raw != wh_raw:
        raise ContainerCreateError(
            "project_mount.host_path and workspace_host_path disagree; pass one consistent path",
        )
    chosen = pm_raw or wh_raw
    if not chosen:
        raise ContainerCreateError(
            "project_mount or workspace_host_path is required to create a workspace container "
            f"(bind-mount host directory to {WORKSPACE_PROJECT_CONTAINER_PATH})",
        )
    if not chosen.startswith("/"):
        raise ContainerCreateError(
            "remote workspace host path must be absolute POSIX path on the execution host; "
            f"got {chosen!r}",
        )
    read_only = bool(project_mount.read_only) if project_mount is not None else False
    return chosen, read_only


class SsmDockerRuntimeAdapter(RuntimeAdapter):
    """
    Container lifecycle via ``docker`` CLI executed through SSM on the target instance.

    ``runner`` is typically :class:`~app.services.node_execution_service.ssm_remote_command_runner.SsmRemoteCommandRunner`.
    """

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def inspect_container(self, *, container_id: str) -> ContainerInspectionResult:
        try:
            out = self._runner.run(["docker", "inspect", container_id])
        except RuntimeError:
            return _inspection_not_found()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return _inspection_not_found()
        if not data or not isinstance(data, list):
            return _inspection_not_found()
        return _normalize_inspection(data[0])

    def ensure_container(
        self,
        *,
        name: str,
        image: str | None = None,
        cpu_limit: float | None = None,
        memory_limit_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
        ports: Sequence[tuple[int, int]] | None = None,
        labels: Mapping[str, str] | None = None,
        project_mount: WorkspaceProjectMountSpec | None = None,
        workspace_host_path: str | None = None,
        extra_bind_mounts: Sequence[WorkspaceExtraBindMountSpec] | None = None,
        existing_container_id: str | None = None,
    ) -> RuntimeEnsureResult:
        if not str(name).strip():
            raise ContainerCreateError("container name must be non-empty")

        rid = (existing_container_id or "").strip()
        if rid:
            ins = self.inspect_container(container_id=rid)
            if ins.exists:
                resolved = ins.ports if ins.ports else ()
                return RuntimeEnsureResult(
                    container_id=ins.container_id or "",
                    exists=True,
                    created_new=False,
                    container_state=ins.container_state,
                    resolved_ports=resolved,
                    node_id=None,
                    workspace_ide_container_port=WORKSPACE_IDE_CONTAINER_PORT,
                    workspace_project_mount=ins.workspace_project_mount,
                )

        ins_name = self.inspect_container(container_id=name)
        if ins_name.exists:
            resolved = ins_name.ports if ins_name.ports else ()
            return RuntimeEnsureResult(
                container_id=ins_name.container_id or "",
                exists=True,
                created_new=False,
                container_state=ins_name.container_state,
                resolved_ports=resolved,
                node_id=None,
                workspace_ide_container_port=WORKSPACE_IDE_CONTAINER_PORT,
                workspace_project_mount=ins_name.workspace_project_mount,
            )

        host_path, proj_ro = _resolve_project_host_path_remote(project_mount, workspace_host_path)
        mode = "ro" if proj_ro else "rw"

        if cpu_limit is not None and cpu_limit <= 0:
            raise ContainerCreateError("cpu_limit must be positive when set")
        if memory_limit_bytes is not None and memory_limit_bytes <= 0:
            raise ContainerCreateError("memory_limit_bytes must be positive when set")

        resolved_image = _resolve_image(image)
        port_bindings = _port_bindings_from_spec(ports)
        try:
            extra = _extra_bind_strings(extra_bind_mounts)
        except ContainerCreateError:
            raise
        env_dict = dict(env) if env else {}
        label_dict = dict(labels) if labels else {}

        try:
            self._runner.run(["docker", "pull", resolved_image])
        except RuntimeError as e:
            raise ContainerCreateError(f"failed to pull image {resolved_image!r}: {e}") from e

        argv = ["docker", "create", "--name", name]
        for k, v in sorted(label_dict.items()):
            argv += ["--label", f"{k}={v}"]
        for k, v in sorted(env_dict.items()):
            argv += ["-e", f"{k}={v}"]
        argv += ["-v", f"{host_path}:{WORKSPACE_PROJECT_CONTAINER_PATH}:{mode}"]
        for b in extra:
            argv += ["-v", b]
        for spec, hp in sorted(port_bindings.items(), key=lambda x: x[0]):
            try:
                cport = int(str(spec).split("/")[0])
            except (TypeError, ValueError):
                continue
            if hp is None:
                argv += ["-p", f"0:{cport}"]
            else:
                argv += ["-p", f"{int(hp)}:{cport}"]
        if cpu_limit is not None:
            cpus = f"{float(cpu_limit):.6f}".rstrip("0").rstrip(".")
            argv += ["--cpus", cpus or "0"]
        if memory_limit_bytes is not None:
            argv += ["--memory", str(int(memory_limit_bytes))]
        argv.append(resolved_image)

        try:
            cid_out = self._runner.run(argv).strip()
        except RuntimeError as e:
            raise ContainerCreateError(str(e)) from e

        lines = [ln.strip() for ln in cid_out.splitlines() if ln.strip()]
        cid = lines[-1] if lines else ""
        if not cid:
            raise ContainerCreateError("docker create returned empty id")

        ins = self.inspect_container(container_id=cid)
        resolved = ins.ports if ins.ports else _resolved_ports_tuple(port_bindings)
        return RuntimeEnsureResult(
            container_id=ins.container_id or cid,
            exists=True,
            created_new=True,
            container_state=ins.container_state,
            resolved_ports=resolved,
            node_id=None,
            workspace_ide_container_port=WORKSPACE_IDE_CONTAINER_PORT,
            workspace_project_mount=ins.workspace_project_mount,
        )

    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists:
            raise ContainerNotFoundError(f"container not found: {container_id!r}")
        if ins.container_state == "running":
            return RuntimeActionResult(
                container_id=ins.container_id or container_id,
                container_state=ins.container_state,
                success=True,
                message=None,
            )
        ins2 = self.inspect_container(container_id=container_id)
        if ins2.container_state == "running":
            return RuntimeActionResult(
                container_id=ins2.container_id or container_id,
                container_state=ins2.container_state,
                success=True,
                message=None,
            )
        try:
            self._runner.run(["docker", "start", container_id])
        except RuntimeError as e:
            raise ContainerStartError(str(e)) from e
        after = self.inspect_container(container_id=container_id)
        return RuntimeActionResult(
            container_id=after.container_id or container_id,
            container_state=after.container_state,
            success=after.container_state == "running",
            message=None
            if after.container_state == "running"
            else f"unexpected state after start: {after.container_state}",
        )

    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists:
            return RuntimeActionResult(
                container_id=container_id,
                container_state="missing",
                success=True,
                message=None,
            )
        cid_out = ins.container_id or container_id
        if not _container_state_needs_engine_stop(ins.container_state):
            return RuntimeActionResult(
                container_id=cid_out,
                container_state=ins.container_state,
                success=True,
                message=None,
            )
        live = self.inspect_container(container_id=container_id)
        if not live.exists:
            return RuntimeActionResult(
                container_id=cid_out,
                container_state="missing",
                success=True,
                message=None,
            )
        cid_live = live.container_id or cid_out
        if not _container_state_needs_engine_stop(live.container_state):
            return RuntimeActionResult(
                container_id=cid_live,
                container_state=live.container_state,
                success=True,
                message=None,
            )
        try:
            self._runner.run(["docker", "stop", "-t", str(_default_stop_timeout_s()), container_id])
        except RuntimeError as e:
            raise ContainerStopError(str(e)) from e
        after = self.inspect_container(container_id=container_id)
        if not after.exists:
            return RuntimeActionResult(
                container_id=cid_out,
                container_state="missing",
                success=True,
                message=None,
            )
        cid_after = after.container_id or container_id
        if _container_state_needs_engine_stop(after.container_state):
            return RuntimeActionResult(
                container_id=cid_after,
                container_state=after.container_state,
                success=False,
                message=f"container still active after stop: {after.container_state}",
            )
        return RuntimeActionResult(
            container_id=cid_after,
            container_state=after.container_state,
            success=True,
            message=None,
        )

    def restart_container(self, *, container_id: str) -> RuntimeActionResult:
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists:
            raise ContainerNotFoundError(f"container not found: {container_id!r}")
        try:
            self._runner.run(["docker", "restart", "-t", str(_default_stop_timeout_s()), container_id])
        except RuntimeError as e:
            raise ContainerStartError(str(e)) from e
        after = self.inspect_container(container_id=container_id)
        if not after.exists:
            raise ContainerNotFoundError(f"container not found after restart: {container_id!r}")
        cid_out = after.container_id or container_id
        ok = after.container_state == "running"
        return RuntimeActionResult(
            container_id=cid_out,
            container_state=after.container_state,
            success=ok,
            message=None if ok else f"unexpected state after restart: {after.container_state}",
        )

    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists:
            return RuntimeActionResult(
                container_id=container_id,
                container_state="missing",
                success=True,
                message=None,
            )
        cid_known = ins.container_id or container_id
        live = self.inspect_container(container_id=container_id)
        if not live.exists:
            return RuntimeActionResult(
                container_id=cid_known,
                container_state="missing",
                success=True,
                message=None,
            )
        cid_known = live.container_id or cid_known
        try:
            if _container_state_needs_engine_stop(live.container_state):
                try:
                    self._runner.run(["docker", "stop", "-t", str(_default_stop_timeout_s()), container_id])
                except RuntimeError as e:
                    raise ContainerStopError(str(e)) from e
            self._runner.run(["docker", "rm", "-f", container_id])
        except RuntimeError as e:
            final = self.inspect_container(container_id=container_id)
            if not final.exists:
                return RuntimeActionResult(
                    container_id=cid_known,
                    container_state="missing",
                    success=True,
                    message=None,
                )
            raise ContainerDeleteError(str(e)) from e
        final = self.inspect_container(container_id=container_id)
        if final.exists:
            return RuntimeActionResult(
                container_id=final.container_id or cid_known,
                container_state=final.container_state,
                success=False,
                message="container still exists after delete",
            )
        return RuntimeActionResult(
            container_id=cid_known,
            container_state="missing",
            success=True,
            message=None,
        )

    def get_container_netns_ref(self, *, container_id: str) -> NetnsRefResult:
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists or not ins.container_id:
            raise NetnsRefError(f"container not found: {container_id!r}")
        if ins.pid is None or ins.pid <= 0:
            raise NetnsRefError(
                f"no host PID for container {ins.container_id!r} (is the container running?)",
            )
        ref = f"/proc/{ins.pid}/ns/net"
        return NetnsRefResult(container_id=ins.container_id, pid=ins.pid, netns_ref=ref)
