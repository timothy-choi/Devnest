"""Docker-backed ``RuntimeAdapter``."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence

import docker
import docker.errors
from docker.models.containers import Container

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
    BindMountInfo,
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
    WORKSPACE_IDE_CONTAINER_PORT,
    WORKSPACE_PROJECT_CONTAINER_PATH,
    WorkspaceExtraBindMountSpec,
    WorkspaceProjectMountSpec,
)

logger = logging.getLogger(__name__)

# Matches Dockerfile.workspace default tag; override with DEVNEST_WORKSPACE_IMAGE.
_DEFAULT_WORKSPACE_IMAGE = "devnest/workspace:latest"
# Seconds between SIGTERM and SIGKILL on ``docker stop`` (override via env).
_DEFAULT_STOP_TIMEOUT_S = 10


def _default_stop_timeout_s() -> int:
    raw = os.environ.get("DEVNEST_RUNTIME_STOP_TIMEOUT_SECONDS", "")
    if raw.strip().isdigit():
        return max(1, int(raw.strip()))
    return _DEFAULT_STOP_TIMEOUT_S


def _container_state_needs_engine_stop(container_state: str) -> bool:
    """States where Docker should receive ``stop`` (running or transient/active)."""
    return container_state in frozenset({"running", "restarting", "paused"})


def _image_cmd_hint_from_attrs(attrs: dict) -> str:
    """Human-readable ``Config.Entrypoint`` / ``Config.Cmd`` for startup failure messages."""
    cfg = attrs.get("Config") or {}
    ep = cfg.get("Entrypoint")
    cmd = cfg.get("Cmd")
    return f"; image entrypoint={ep!r} cmd={cmd!r}"


def _default_workspace_image() -> str:
    return os.environ.get("DEVNEST_WORKSPACE_IMAGE", _DEFAULT_WORKSPACE_IMAGE).strip() or _DEFAULT_WORKSPACE_IMAGE


def _resolve_image(image: str | None) -> str:
    if image is None or not str(image).strip():
        return _default_workspace_image()
    return str(image).strip()


def _inspection_not_found() -> ContainerInspectionResult:
    return ContainerInspectionResult(
        exists=False,
        container_id=None,
        container_state="missing",
        pid=None,
        ports=(),
        mounts=(),
        health_status=None,
        bind_mounts=(),
        workspace_project_mount=None,
        labels=(),
        started_at=None,
        finished_at=None,
        exit_code=None,
    )


def _normalize_inspection_labels(attrs: dict) -> tuple[tuple[str, str], ...]:
    cfg = attrs.get("Config")
    raw: dict | None = None
    if isinstance(cfg, dict):
        raw_labels = cfg.get("Labels")
        if isinstance(raw_labels, dict):
            raw = raw_labels
    if not raw:
        return ()
    pairs: list[tuple[str, str]] = []
    for k, v in raw.items():
        if k is None:
            continue
        pairs.append((str(k), str(v) if v is not None else ""))
    return tuple(sorted(pairs))


def _normalize_engine_state(raw: object | None) -> str:
    """
    Map Docker ``State.Status`` (or equivalent) to a stable lowercase token.

    Docker Engine commonly reports: created, running, paused, restarting, removing, exited, dead.
    Missing or empty values become ``unknown``.
    """
    if raw is None:
        return "unknown"
    s = str(raw).strip()
    if not s:
        return "unknown"
    return s.lower()


def _normalize_ports(attrs: dict) -> tuple[tuple[int, int], ...]:
    out: list[tuple[int, int]] = []
    net_ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
    for container_spec, bindings in net_ports.items():
        try:
            cport = int(str(container_spec).split("/")[0])
        except (TypeError, ValueError):
            continue
        if not bindings:
            continue
        for b in bindings:
            if not b:
                continue
            hp = b.get("HostPort")
            if hp is not None:
                hs = str(hp).strip()
                if hs.isdigit():
                    out.append((int(hs), cport))
    return tuple(sorted(out, key=lambda p: (p[0], p[1])))


def _normalize_mounts(attrs: dict) -> tuple[str, ...]:
    rows: list[str] = []
    for m in attrs.get("Mounts") or []:
        if not isinstance(m, dict):
            continue
        dest = m.get("Destination") or ""
        src = m.get("Source") or ""
        rows.append(f"{src}:{dest}" if src else (dest or ""))
    return tuple(rows)


def _normalize_health(attrs: dict) -> str | None:
    health = (attrs.get("State") or {}).get("Health") or {}
    status = health.get("Status")
    if not status:
        return None
    return str(status).lower()


def _normalize_bind_mount_infos(attrs: dict) -> tuple[BindMountInfo, ...]:
    out: list[BindMountInfo] = []
    for m in attrs.get("Mounts") or []:
        if not isinstance(m, dict):
            continue
        if str(m.get("Type") or "").lower() != "bind":
            continue
        dest = str(m.get("Destination") or "").strip()
        src = str(m.get("Source") or "").strip()
        if not dest:
            continue
        read_only = m.get("RW") is False
        prop_raw = m.get("Propagation")
        propagation: str | None
        if prop_raw is None or str(prop_raw).strip() == "":
            propagation = None
        else:
            propagation = str(prop_raw).strip()
        out.append(
            BindMountInfo(
                host_path=src,
                container_path=dest,
                read_only=read_only,
                propagation=propagation,
            ),
        )
    return tuple(out)


def _workspace_project_bind(bind_mounts: tuple[BindMountInfo, ...]) -> BindMountInfo | None:
    suffix = WORKSPACE_PROJECT_CONTAINER_PATH.rstrip("/")
    for b in bind_mounts:
        if b.container_path.rstrip("/") == suffix:
            return b
    return None


def _bind_mount_signature(bind: BindMountInfo) -> tuple[str, str, bool]:
    return (
        str(bind.host_path).strip(),
        str(bind.container_path).rstrip("/"),
        bool(bind.read_only),
    )


def _requested_bind_signatures(
    workspace_host_path: str | None,
    extra_bind_mounts: Sequence[WorkspaceExtraBindMountSpec] | None,
) -> tuple[tuple[str, str, bool], ...] | None:
    requested: list[tuple[str, str, bool]] = []
    if workspace_host_path and str(workspace_host_path).strip():
        host_path, proj_ro = _resolve_project_host_path_for_create(None, workspace_host_path)
        requested.append((host_path, WORKSPACE_PROJECT_CONTAINER_PATH, proj_ro))
    if extra_bind_mounts:
        for spec in extra_bind_mounts:
            hp = str(spec.host_path or "").strip()
            cp = str(spec.container_path or "").strip().rstrip("/")
            requested.append((hp, cp, bool(spec.read_only)))
    if not requested:
        return None
    return tuple(sorted(requested))


def _existing_bind_signatures(bind_mounts: tuple[BindMountInfo, ...]) -> tuple[tuple[str, str, bool], ...]:
    return tuple(sorted(_bind_mount_signature(b) for b in bind_mounts))


def _resolve_project_host_path_for_create(
    project_mount: WorkspaceProjectMountSpec | None,
    workspace_host_path: str | None,
) -> tuple[str, bool]:
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
    if not os.path.isabs(chosen):
        raise ContainerCreateError(
            "project workspace host path must be absolute (Docker bind source); "
            f"sync storage layout before ensure_container, got {chosen!r}",
        )
    read_only = bool(project_mount.read_only) if project_mount is not None else False
    return chosen, read_only


def _extra_bind_strings(
    extra_bind_mounts: Sequence[WorkspaceExtraBindMountSpec] | None,
) -> list[str]:
    """
    Docker ``HostConfig`` bind strings for optional mounts; project mount is handled separately.

    Order is preserved: callers usually list config before data
    (``CODE_SERVER_OPTIONAL_PERSISTENCE_CONTAINER_PATHS``). Any absolute ``container_path`` is
    allowed; dup destinations and the project path are rejected.
    """
    if not extra_bind_mounts:
        return []
    project_norm = WORKSPACE_PROJECT_CONTAINER_PATH.rstrip("/")
    seen_dest: set[str] = set()
    out: list[str] = []
    for spec in extra_bind_mounts:
        hp = str(spec.host_path).strip()
        cp = str(spec.container_path).strip()
        if not hp or not cp:
            raise ContainerCreateError(
                "extra_bind_mounts requires non-empty host_path and container_path on every entry",
            )
        dest_key = cp.rstrip("/") or "/"
        if dest_key == project_norm:
            raise ContainerCreateError(
                "extra_bind_mounts cannot target the project mount path "
                f"({WORKSPACE_PROJECT_CONTAINER_PATH}); use project_mount / workspace_host_path only",
            )
        if dest_key in seen_dest:
            raise ContainerCreateError(f"duplicate extra_bind_mounts container_path: {cp!r}")
        seen_dest.add(dest_key)
        mode = "ro" if spec.read_only else "rw"
        out.append(f"{hp}:{cp}:{mode}")
    return out


def _normalize_inspection(attrs: dict) -> ContainerInspectionResult:
    state = attrs.get("State") or {}
    status = _normalize_engine_state(state.get("Status"))
    raw_pid = state.get("Pid")
    pid: int | None
    if raw_pid is None or raw_pid == 0:
        pid = None
    else:
        try:
            p = int(raw_pid)
        except (TypeError, ValueError):
            pid = None
        else:
            pid = None if p <= 0 else p

    started_raw = state.get("StartedAt")
    finished_raw = state.get("FinishedAt")
    started_at = str(started_raw).strip() if started_raw not in (None, "") else None
    finished_at = str(finished_raw).strip() if finished_raw not in (None, "") else None
    raw_exit = state.get("ExitCode")
    exit_code: int | None
    if raw_exit is None:
        exit_code = None
    else:
        try:
            exit_code = int(raw_exit)
        except (TypeError, ValueError):
            exit_code = None

    cid = attrs.get("Id")
    if cid is None:
        container_id = None
    else:
        sid = str(cid).strip()
        container_id = sid or None

    bind_mounts = _normalize_bind_mount_infos(attrs)
    return ContainerInspectionResult(
        exists=True,
        container_id=container_id,
        container_state=status,
        pid=pid,
        ports=_normalize_ports(attrs),
        mounts=_normalize_mounts(attrs),
        health_status=_normalize_health(attrs),
        bind_mounts=bind_mounts,
        workspace_project_mount=_workspace_project_bind(bind_mounts),
        labels=_normalize_inspection_labels(attrs),
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
    )


def _port_bindings_from_spec(ports: Sequence[tuple[int, int]] | None) -> dict[str, int | None]:
    """
    Host publish map only: ``container_port/tcp`` -> host port, or ``None`` for ephemeral.

    When ``ports`` is omitted or empty, **no** ports are published to the host (workspace IDE
    still listens on ``WORKSPACE_IDE_CONTAINER_PORT`` inside the container network namespace).

    When ``ports`` is provided, each entry is ``(host_port, container_port)``: use a positive
    ``host_port`` to pin a **distinct** free port per workspace on shared hosts, or ``<= 0`` for an
    engine-assigned ephemeral host port (recommended when many workspaces run on one host). There
    is **no** implicit host publish; pinning e.g. host 8080 is opt-in only and must not be assumed.
    Duplicate container ports in one spec: last binding wins.
    """
    bindings: dict[str, int | None] = {}
    if ports:
        for host_p, cont_p in ports:
            cp = int(cont_p)
            if cp <= 0:
                raise ContainerCreateError(f"container port in ports= must be positive, got {cp!r}")
            key = f"{cp}/tcp"
            hp = int(host_p)
            bindings[key] = None if hp <= 0 else hp
    return bindings


def _resolved_ports_tuple(bindings: dict[str, int | None]) -> tuple[tuple[int, int], ...]:
    """Pairs where host port is known from the create spec only (ephemeral ``None`` entries omitted)."""
    out: list[tuple[int, int]] = []
    for spec, hp in bindings.items():
        if hp is None:
            continue
        try:
            cport = int(str(spec).split("/")[0])
        except (TypeError, ValueError):
            continue
        out.append((int(hp), cport))
    return tuple(sorted(out))


def _exposed_container_ports(bindings: dict[str, int | None]) -> list[int]:
    ports_set: set[int] = set()
    for spec in bindings:
        try:
            ports_set.add(int(str(spec).split("/")[0]))
        except (TypeError, ValueError):
            continue
    return sorted(ports_set)


class DockerRuntimeAdapter(RuntimeAdapter):
    """
    Talks to the local Docker engine via the official ``docker`` SDK.

    Inject a ``DockerClient`` for tests; default is ``docker.from_env()``.
    """

    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client if client is not None else docker.from_env()

    @property
    def docker_engine_client(self) -> docker.DockerClient:
        """Underlying :class:`docker.DockerClient` (local or ``ssh://`` remote engine)."""
        return self._client

    def _get_container_if_exists(self, ref: str) -> Container | None:
        try:
            return self._client.containers.get(ref)
        except docker.errors.NotFound:
            return None

    def inspect_container(self, *, container_id: str) -> ContainerInspectionResult:
        """
        Read-only inspect by container id or name: returns ``ContainerInspectionResult`` only.

        ``docker.errors.NotFound`` maps to ``exists=False``; other engine errors propagate.
        """
        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound:
            return _inspection_not_found()
        return _normalize_inspection(ctr.attrs)

    def fetch_container_log_tail(self, *, container_id: str, lines: int = 80) -> str:
        n = max(1, min(int(lines), 4096))
        try:
            ctr = self._client.containers.get(container_id)
            raw = ctr.logs(tail=n)
        except Exception:
            return ""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

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
        """
        Ensure a workspace container: reuse by ``existing_container_id`` or ``name``, else create.

        **Reuse:** Returns inspection-based ``resolved_ports`` and ``workspace_project_mount``;
        ``image``, ``ports``, ``project_mount``, ``workspace_host_path``, and ``extra_bind_mounts``
        are **not** applied (callers rely on the existing container; replace/delete is out of band).

        **Create:** ``_resolve_image`` selects ``image`` or ``DEVNEST_WORKSPACE_IMAGE`` /
        ``devnest/workspace:latest``. Binds are ``[project, *extra_bind_mounts]`` to
        ``WORKSPACE_PROJECT_CONTAINER_PATH`` (``/home/coder/project``) then optional binds from
        ``extra_bind_mounts`` (e.g. code-server config/state via
        ``CODE_SERVER_OPTIONAL_PERSISTENCE_CONTAINER_PATHS``—never auto-applied). The project bind
        is always first in ``HostConfig`` binds (``rw``/``ro`` from ``WorkspaceProjectMountSpec``
        when used, else ``rw``). Host publishes come
        only from explicit ``ports`` (never an implicit default). Each workspace may use
        ``host_port <= 0`` for ephemeral host ports so multiple containers do not share one fixed
        host binding. ``workspace_ide_container_port`` on the result is always
        ``WORKSPACE_IDE_CONTAINER_PORT`` (in-container IDE only); ``resolved_ports`` lists
        host-published ``(host_port, container_port)`` pairs when the engine reports them (often
        fully populated after the process is running and port maps exist).
        """
        if not str(name).strip():
            raise ContainerCreateError("container name must be non-empty")

        existing: Container | None = None
        rid = (existing_container_id or "").strip()
        if rid:
            existing = self._get_container_if_exists(rid)
        if existing is None:
            existing = self._get_container_if_exists(name)

        if existing is not None:
            ins = _normalize_inspection(existing.attrs)
            requested_binds = _requested_bind_signatures(workspace_host_path, extra_bind_mounts)
            existing_binds = _existing_bind_signatures(ins.bind_mounts)
            if requested_binds is not None and existing_binds != requested_binds:
                logger.warning(
                    "workspace_runtime_container_recreate_for_bind_mismatch",
                    extra={
                        "container_name": name,
                        "container_id": ins.container_id,
                        "requested_binds": requested_binds,
                        "existing_binds": existing_binds,
                    },
                )
                try:
                    existing.remove(force=True)
                except docker.errors.NotFound:
                    pass
                except docker.errors.APIError as e:
                    raise ContainerCreateError(
                        f"failed to replace stale container {name!r} with mismatched bind mounts: {e}",
                    ) from e
                existing = None

        if existing is not None:
            ins = _normalize_inspection(existing.attrs)
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

        host_path, proj_ro = _resolve_project_host_path_for_create(project_mount, workspace_host_path)
        if proj_ro:
            mode = "ro"
        else:
            mode = "rw"

        if cpu_limit is not None and cpu_limit <= 0:
            raise ContainerCreateError("cpu_limit must be positive when set")
        if memory_limit_bytes is not None and memory_limit_bytes <= 0:
            raise ContainerCreateError("memory_limit_bytes must be positive when set")

        resolved_image = _resolve_image(image)
        port_bindings = _port_bindings_from_spec(ports)
        binds = [f"{host_path}:{WORKSPACE_PROJECT_CONTAINER_PATH}:{mode}", *_extra_bind_strings(extra_bind_mounts)]
        hc_kwargs: dict = {
            "binds": binds,
        }
        if port_bindings:
            hc_kwargs["port_bindings"] = port_bindings
        if cpu_limit is not None:
            hc_kwargs["nano_cpus"] = int(cpu_limit * 1_000_000_000)
        if memory_limit_bytes is not None:
            hc_kwargs["mem_limit"] = int(memory_limit_bytes)

        host_config = self._client.api.create_host_config(**hc_kwargs)
        env_dict = dict(env) if env else {}
        label_dict = dict(labels) if labels else {}

        def _create():
            # High-level ``containers.create`` rejects ``host_config`` on docker-py 7+; use the API client.
            resp = self._client.api.create_container(
                image=resolved_image,
                name=name,
                detach=True,
                environment=env_dict,
                labels=label_dict,
                host_config=host_config,
                ports=_exposed_container_ports(port_bindings),
            )
            cid = resp.get("Id") or resp.get("id")
            if not cid:
                raise ContainerCreateError("create_container returned no Id")
            return self._client.containers.get(cid)

        try:
            ctr = _create()
        except docker.errors.ImageNotFound:
            try:
                self._client.images.pull(resolved_image)
            except docker.errors.APIError as pull_err:
                raise ContainerCreateError(f"failed to pull image {resolved_image!r}: {pull_err}") from pull_err
            try:
                ctr = _create()
            except docker.errors.APIError as create_err:
                raise ContainerCreateError(str(create_err)) from create_err
        except docker.errors.APIError as e:
            raise ContainerCreateError(str(e)) from e

        ctr.reload()
        ins = _normalize_inspection(ctr.attrs)
        resolved = ins.ports if ins.ports else _resolved_ports_tuple(port_bindings)
        logger.info(
            "workspace_runtime_docker_create",
            extra={
                "container_id": ins.container_id,
                "container_state": ins.container_state,
                "started_at": ins.started_at,
                "finished_at": ins.finished_at,
                "pid": ins.pid,
            },
        )
        return RuntimeEnsureResult(
            container_id=ins.container_id or "",
            exists=True,
            created_new=True,
            container_state=ins.container_state,
            resolved_ports=resolved,
            node_id=None,
            workspace_ide_container_port=WORKSPACE_IDE_CONTAINER_PORT,
            workspace_project_mount=ins.workspace_project_mount,
        )

    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Inspect-first, idempotent start: missing → ``ContainerNotFoundError``; already running →
        success without calling ``start``; after a successful ``get``, ``reload`` + normalize so a
        race where the container entered ``running`` still avoids a redundant ``start``; otherwise
        ``start`` then re-inspect for a normalized ``RuntimeActionResult``.

        Host port bindings are not part of ``RuntimeActionResult``; use ``inspect_container`` for
        normalized ``ports`` when publish maps are required after start.
        """
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

        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound as e:
            raise ContainerNotFoundError(f"container not found: {container_id!r}") from e

        ctr.reload()
        live = _normalize_inspection(ctr.attrs)
        if live.container_state == "running":
            return RuntimeActionResult(
                container_id=live.container_id or container_id,
                container_state=live.container_state,
                success=True,
                message=None,
            )

        try:
            ctr.start()
        except docker.errors.APIError as e:
            raise ContainerStartError(str(e)) from e

        after = self.inspect_container(container_id=container_id)
        logger.info(
            "workspace_runtime_docker_start",
            extra={
                "container_id": after.container_id or container_id,
                "container_state": after.container_state,
                "pid": after.pid,
                "started_at": after.started_at,
                "finished_at": after.finished_at,
                "exit_code": after.exit_code,
            },
        )
        if after.container_state == "running":
            return RuntimeActionResult(
                container_id=after.container_id or container_id,
                container_state=after.container_state,
                success=True,
                message=None,
            )
        hint = ""
        try:
            ctr = self._client.containers.get(container_id)
            hint = _image_cmd_hint_from_attrs(ctr.attrs)
        except Exception:
            pass
        return RuntimeActionResult(
            container_id=after.container_id or container_id,
            container_state=after.container_state,
            success=False,
            message=f"unexpected state after start: {after.container_state}{hint}",
        )

    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Inspect-first stop, idempotent for cleanup: missing container → success; non-active
        states → success without ``stop``; when the engine ``get`` succeeds, ``reload`` + normalize
        so a stale “needs stop” inspect still skips ``stop`` if the container is already inactive;
        else ``stop(timeout)`` then re-inspect.
        """
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

        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound:
            return RuntimeActionResult(
                container_id=cid_out,
                container_state="missing",
                success=True,
                message=None,
            )

        ctr.reload()
        live = _normalize_inspection(ctr.attrs)
        cid_live = live.container_id or cid_out
        if not _container_state_needs_engine_stop(live.container_state):
            return RuntimeActionResult(
                container_id=cid_live,
                container_state=live.container_state,
                success=True,
                message=None,
            )

        try:
            ctr.stop(timeout=_default_stop_timeout_s())
        except docker.errors.APIError as e:
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
        """
        Inspect-first restart: missing → ``ContainerNotFoundError``; else engine ``get``,
        ``reload`` for a fresh snapshot, then ``restart`` with the same stop-timeout budget as
        ``stop_container``, then re-inspect.
        """
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists:
            raise ContainerNotFoundError(f"container not found: {container_id!r}")

        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound as e:
            raise ContainerNotFoundError(f"container not found: {container_id!r}") from e

        ctr.reload()

        try:
            ctr.restart(timeout=_default_stop_timeout_s())
        except docker.errors.APIError as e:
            raise ContainerStartError(str(e)) from e

        after = self.inspect_container(container_id=container_id)
        if not after.exists:
            raise ContainerNotFoundError(f"container not found after restart: {container_id!r}")
        cid_out = after.container_id or container_id
        ok = after.container_state == "running"
        if ok:
            return RuntimeActionResult(
                container_id=cid_out,
                container_state=after.container_state,
                success=True,
                message=None,
            )
        hint = ""
        try:
            ctr = self._client.containers.get(container_id)
            hint = _image_cmd_hint_from_attrs(ctr.attrs)
        except Exception:
            pass
        return RuntimeActionResult(
            container_id=cid_out,
            container_state=after.container_state,
            success=False,
            message=f"unexpected state after restart: {after.container_state}{hint}",
        )

    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        """
        Idempotent delete: missing → success; after ``get``, ``reload`` + normalize and decide
        graceful ``stop`` from that live state (not the first inspect snapshot), then ``remove``;
        re-inspect to confirm removal.
        """
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists:
            return RuntimeActionResult(
                container_id=container_id,
                container_state="missing",
                success=True,
                message=None,
            )

        cid_known = ins.container_id or container_id

        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound:
            return RuntimeActionResult(
                container_id=cid_known,
                container_state="missing",
                success=True,
                message=None,
            )

        ctr.reload()
        live = _normalize_inspection(ctr.attrs)
        cid_known = live.container_id or cid_known

        try:
            if _container_state_needs_engine_stop(live.container_state):
                try:
                    ctr.stop(timeout=_default_stop_timeout_s())
                except docker.errors.APIError as e:
                    raise ContainerStopError(str(e)) from e
            ctr.remove()
        except docker.errors.NotFound:
            return RuntimeActionResult(
                container_id=cid_known,
                container_state="missing",
                success=True,
                message=None,
            )
        except docker.errors.APIError as e:
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
        """
        Inspect-only bridge for future topology: resolve the host ``net`` namespace path from the
        container's init PID. No ``setns``, veth, or routing; callers use the returned path later.

        Uses Docker ``State.Pid`` (host PID namespace, container init) per Engine API; ``netns_ref``
        is the Linux path ``/proc/<pid>/ns/net`` on the Docker host.

        Retries briefly while the engine reports running but PID is not yet visible (startup race).

        Raises:
            NetnsRefError: Missing container, or no valid host PID (typically when not running).
        """
        last_ins: ContainerInspectionResult | None = None
        for _ in range(50):
            ins = self.inspect_container(container_id=container_id)
            last_ins = ins
            if not ins.exists or not ins.container_id:
                raise NetnsRefError(f"container not found: {container_id!r}")
            if ins.pid is not None and ins.pid > 0:
                # /proc only exists on Linux; topology attach is Linux-only in production.
                if sys.platform != "linux" or os.path.isdir(f"/proc/{ins.pid}"):
                    ref = f"/proc/{ins.pid}/ns/net"
                    return NetnsRefResult(container_id=ins.container_id, pid=ins.pid, netns_ref=ref)
                raise NetnsRefError(
                    f"host PID {ins.pid} for container {ins.container_id!r} is not visible under "
                    f"/proc/{ins.pid} in this process namespace — run the control plane with "
                    f"`pid: host` (Docker Compose) so `ip link set … netns` can resolve workspace PIDs",
                )
            if ins.container_state not in ("running", "restarting", "created", "paused"):
                break
            time.sleep(0.1)
        ins = last_ins
        assert ins is not None
        log_hint = ""
        cid = ins.container_id or container_id
        if ins.container_state == "exited":
            try:
                ctr = self._client.containers.get(cid)
                raw = ctr.logs(tail=48).decode("utf-8", errors="replace").strip()
                if raw:
                    tail = raw[-2000:]
                    log_hint = (
                        "\n--- workspace container log tail (runtime startup; not topology) ---\n"
                        f"{tail}"
                    )
            except Exception:
                log_hint = ""
        raise NetnsRefError(
            f"no host PID for container {cid!r} after waiting (state={ins.container_state!r}). "
            "If the container exited immediately, this is usually a workspace runtime startup failure "
            "(e.g. code-server cannot write bind-mounted config under /home/coder/.config/code-server), "
            f"not a topology attachment issue.{log_hint}",
        )
