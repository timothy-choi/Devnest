"""Internal runtime-only orchestration: sequence ``RuntimeAdapter`` calls (no DB, topology, or HTTP)."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence

from .errors import ContainerCreateError, ContainerStartError
from .interfaces import RuntimeAdapter
from .models import (
    ContainerInspectionResult,
    EnsureRunningRuntimeResult,
    NetnsRefResult,
    WorkspaceExtraBindMountSpec,
    WorkspaceProjectMountSpec,
)

# Placeholder when ``skip_netns_resolution=True`` (CI / dev: Linux veth attachment disabled).
_SKIP_NETNS_PLACEHOLDER_REF = "/devnest-skip-linux-topology-attachment"

logger = logging.getLogger(__name__)


def _workspace_container_log_tail(runtime: RuntimeAdapter, container_id: str, *, lines: int = 200) -> str:
    try:
        raw = runtime.fetch_container_log_tail(container_id=container_id, lines=lines)
    except Exception:
        return ""
    if not isinstance(raw, str):
        return str(raw)
    return raw.strip()


def _inspect_brief(ins: ContainerInspectionResult) -> dict[str, object | None]:
    return {
        "container_id": ins.container_id,
        "state": ins.container_state,
        "pid": ins.pid,
        "started_at": ins.started_at,
        "finished_at": ins.finished_at,
        "exit_code": ins.exit_code,
    }


def _wait_post_start_inspect(
    runtime: RuntimeAdapter,
    container_id: str,
    *,
    skip_netns_resolution: bool,
) -> ContainerInspectionResult:
    """
    After ``start_container`` succeeds, detect an immediate crash and wait briefly for PID.

    Surfaces workspace startup failures (e.g. code-server EACCES) before topology attach, instead of
    only failing later with a misleading netns / attach error.
    """
    deadline = time.monotonic() + 2.0
    last: ContainerInspectionResult | None = None
    while time.monotonic() < deadline:
        ins = runtime.inspect_container(container_id=container_id)
        last = ins
        logger.info(
            "workspace_runtime_post_start_inspect",
            extra={"phase": "post_start_poll", "container_id": container_id, **_inspect_brief(ins)},
        )
        if ins.container_state in ("exited", "dead"):
            tail = _workspace_container_log_tail(runtime, container_id, lines=200)
            detail = (
                f"workspace container exited immediately after start "
                f"(state={ins.container_state!r}, pid={ins.pid!r}, "
                f"exit_code={ins.exit_code!r}, started_at={ins.started_at!r}, finished_at={ins.finished_at!r}). "
                "Typical causes: code-server EACCES on bind-mounted config/data, bad ENTRYPOINT/CMD, or OOM. "
                "If you only see exit 143 after a failed bring-up, orchestrator rollback issued SIGTERM via "
                "`docker stop` — capture logs below *before* assuming 143 is the primary failure."
            )
            if tail:
                detail = f"{detail}\n--- workspace container log tail ---\n{tail[-8000:]}"
            raise ContainerStartError(detail)
        if ins.container_state == "running":
            if skip_netns_resolution:
                return ins
            if ins.pid is not None and ins.pid > 0:
                return ins
        time.sleep(0.05)
    if last is None:
        raise ContainerStartError("inspect_container returned no result after start")
    if not skip_netns_resolution:
        if last.container_state in ("exited", "dead"):
            tail = _workspace_container_log_tail(runtime, container_id, lines=200)
            detail = (
                f"workspace container exited after start "
                f"(state={last.container_state!r}, pid={last.pid!r}, "
                f"exit_code={last.exit_code!r}, started_at={last.started_at!r}, finished_at={last.finished_at!r}). "
                "If exit_code is 143 after bring-up failure, rollback SIGTERM is likely — see log tail for the "
                "original startup error."
            )
            if tail:
                detail = f"{detail}\n--- workspace container log tail ---\n{tail[-8000:]}"
            raise ContainerStartError(detail)
        if last.container_state != "running" or last.pid is None or last.pid <= 0:
            tail = _workspace_container_log_tail(runtime, container_id, lines=200)
            detail = (
                f"workspace container did not reach running with a host PID within the post-start window "
                f"(state={last.container_state!r}, pid={last.pid!r}, "
                f"exit_code={last.exit_code!r}, started_at={last.started_at!r}, finished_at={last.finished_at!r}). "
                "Topology attach requires a live init PID — fix workspace startup first, then retry."
            )
            if tail:
                detail = f"{detail}\n--- workspace container log tail ---\n{tail[-8000:]}"
            raise ContainerStartError(detail)
    return last


def ensure_running_runtime_only(
    runtime: RuntimeAdapter,
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
    skip_netns_resolution: bool = False,
) -> EnsureRunningRuntimeResult:
    """
    Narrow orchestrator slice: bring a container to a running state and collect runtime facts.

    **Call order:** ``ensure_container`` → ``start_container`` → ``inspect_container`` →
    ``get_container_netns_ref`` (skipped when ``skip_netns_resolution`` is true).

    **Raises:** ``ContainerCreateError`` (empty id after ensure), ``ContainerStartError`` (start
    not successful), ``NetnsRefError`` (from ``get_container_netns_ref`` if PID/netns unavailable,
    unless ``skip_netns_resolution``).

    Does not write the database, attach topology, compute public URLs, or register gateway routes.
    Callers persist workspace / routing state separately.
    """
    ensure_res = runtime.ensure_container(
        name=name,
        image=image,
        cpu_limit=cpu_limit,
        memory_limit_bytes=memory_limit_bytes,
        env=env,
        ports=ports,
        labels=labels,
        project_mount=project_mount,
        workspace_host_path=workspace_host_path,
        extra_bind_mounts=extra_bind_mounts,
        existing_container_id=existing_container_id,
    )
    if not ensure_res.container_id:
        raise ContainerCreateError("ensure_container returned an empty container_id")

    cid0 = ensure_res.container_id
    logger.info(
        "workspace_runtime_sequence",
        extra={
            "phase": "after_ensure_container_before_start",
            "container_id": cid0,
            "created_new": ensure_res.created_new,
            "state_after_ensure": ensure_res.container_state,
        },
    )

    start_res = runtime.start_container(container_id=cid0)
    if not start_res.success:
        tail = _workspace_container_log_tail(runtime, cid0, lines=200)
        msg = start_res.message or "container did not reach running state"
        if tail:
            msg = f"{msg}\n--- workspace container log tail ---\n{tail[-8000:]}"
        raise ContainerStartError(msg)

    logger.info(
        "workspace_runtime_sequence",
        extra={
            "phase": "after_docker_start_api_success",
            "container_id": start_res.container_id,
            "reported_state": start_res.container_state,
        },
    )

    inspected = _wait_post_start_inspect(
        runtime,
        ensure_res.container_id,
        skip_netns_resolution=skip_netns_resolution,
    )
    logger.info(
        "workspace_runtime_sequence",
        extra={
            "phase": "after_post_start_inspect_ok",
            "container_id": inspected.container_id or cid0,
            **_inspect_brief(inspected),
        },
    )
    cid_for_netns = inspected.container_id or ensure_res.container_id
    if skip_netns_resolution:
        netns = NetnsRefResult(
            container_id=cid_for_netns,
            pid=0,
            netns_ref=_SKIP_NETNS_PLACEHOLDER_REF,
        )
    else:
        netns = runtime.get_container_netns_ref(container_id=ensure_res.container_id)
        logger.info(
            "workspace_runtime_sequence",
            extra={
                "phase": "after_get_container_netns_ref",
                "container_id": netns.container_id,
                "netns_pid": netns.pid,
            },
        )

    container_id = inspected.container_id or ensure_res.container_id
    resolved_ports = inspected.ports if inspected.ports else ensure_res.resolved_ports

    return EnsureRunningRuntimeResult(
        container_id=container_id,
        container_state=inspected.container_state,
        pid=netns.pid,
        netns_ref=netns.netns_ref,
        resolved_ports=resolved_ports,
        node_id=ensure_res.node_id,
        workspace_ide_container_port=ensure_res.workspace_ide_container_port,
        workspace_project_mount=inspected.workspace_project_mount or ensure_res.workspace_project_mount,
    )
