"""Docker-backed ``RuntimeAdapter``."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

import docker
import docker.errors
from docker.types import HostConfig

from .errors import ContainerCreateError, ContainerNotFoundError, ContainerStartError, NetnsRefError
from .interfaces import RuntimeAdapter
from .models import ContainerInspectionResult, NetnsRefResult, RuntimeActionResult, RuntimeEnsureResult

# Matches Dockerfile.workspace default tag; override with DEVNEST_WORKSPACE_IMAGE.
_DEFAULT_WORKSPACE_IMAGE = "devnest/workspace:latest"
_WORKSPACE_MOUNT_TARGET = "/home/coder/project"
_WORKSPACE_CONTAINER_PORT = 8080


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
    )


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
            if hp is not None and str(hp).isdigit():
                out.append((int(hp), cport))
    return tuple(out)


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


def _normalize_inspection(attrs: dict) -> ContainerInspectionResult:
    state = attrs.get("State") or {}
    status = (state.get("Status") or "unknown").lower()
    raw_pid = state.get("Pid")
    pid: int | None
    if raw_pid is None or raw_pid == 0:
        pid = None
    else:
        pid = int(raw_pid)

    cid = attrs.get("Id")
    container_id = str(cid) if cid else None

    return ContainerInspectionResult(
        exists=True,
        container_id=container_id,
        container_state=status,
        pid=pid,
        ports=_normalize_ports(attrs),
        mounts=_normalize_mounts(attrs),
        health_status=_normalize_health(attrs),
    )


def _port_bindings_from_spec(ports: Sequence[tuple[int, int]] | None) -> dict[str, int]:
    """Map ``container_port/tcp`` -> host port; always publishes workspace port 8080."""
    bindings: dict[str, int] = {}
    if ports:
        for host_p, cont_p in ports:
            bindings[f"{int(cont_p)}/tcp"] = int(host_p)
    key = f"{_WORKSPACE_CONTAINER_PORT}/tcp"
    if key not in bindings:
        bindings[key] = _WORKSPACE_CONTAINER_PORT
    return bindings


def _resolved_ports_tuple(bindings: dict[str, int]) -> tuple[tuple[int, int], ...]:
    out: list[tuple[int, int]] = []
    for spec, hp in bindings.items():
        try:
            cport = int(str(spec).split("/")[0])
        except (TypeError, ValueError):
            continue
        out.append((int(hp), cport))
    return tuple(sorted(out))


def _exposed_container_ports(bindings: dict[str, int]) -> list[int]:
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

    def inspect_container(self, *, container_id: str) -> ContainerInspectionResult:
        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound:
            return _inspection_not_found()
        return _normalize_inspection(ctr.attrs)

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
        workspace_host_path: str | None = None,
    ) -> RuntimeEnsureResult:
        try:
            existing = self._client.containers.get(name)
        except docker.errors.NotFound:
            existing = None

        if existing is not None:
            ins = _normalize_inspection(existing.attrs)
            return RuntimeEnsureResult(
                container_id=ins.container_id or "",
                exists=True,
                created_new=False,
                container_state=ins.container_state,
                resolved_ports=ins.ports if ins.ports else _resolved_ports_tuple(_port_bindings_from_spec(ports)),
                node_id=None,
            )

        if not workspace_host_path or not str(workspace_host_path).strip():
            raise ContainerCreateError(
                "workspace_host_path is required to create a new workspace container "
                f"(bind-mount host path to {_WORKSPACE_MOUNT_TARGET})",
            )

        resolved_image = _resolve_image(image)
        port_bindings = _port_bindings_from_spec(ports)
        hc_kwargs: dict = {"port_bindings": port_bindings}
        hc_kwargs["binds"] = [f"{str(workspace_host_path).strip()}:{_WORKSPACE_MOUNT_TARGET}:rw"]
        if cpu_limit is not None:
            hc_kwargs["nano_cpus"] = int(cpu_limit * 1_000_000_000)
        if memory_limit_bytes is not None:
            hc_kwargs["mem_limit"] = int(memory_limit_bytes)

        host_config = HostConfig(**hc_kwargs)
        env_dict = dict(env) if env else {}
        label_dict = dict(labels) if labels else {}

        def _create() -> docker.models.containers.Container:
            return self._client.containers.create(
                image=resolved_image,
                name=name,
                detach=True,
                environment=env_dict,
                labels=label_dict,
                host_config=host_config,
                ports=_exposed_container_ports(port_bindings),
            )

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

        ins = _normalize_inspection(ctr.attrs)
        resolved = ins.ports if ins.ports else _resolved_ports_tuple(port_bindings)
        return RuntimeEnsureResult(
            container_id=ins.container_id or "",
            exists=True,
            created_new=True,
            container_state=ins.container_state,
            resolved_ports=resolved,
            node_id=None,
        )

    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        try:
            ctr = self._client.containers.get(container_id)
        except docker.errors.NotFound as e:
            raise ContainerNotFoundError(f"container not found: {container_id!r}") from e

        ctr.reload()
        if ctr.status == "running":
            ins = _normalize_inspection(ctr.attrs)
            return RuntimeActionResult(
                container_id=ins.container_id or container_id,
                container_state=ins.container_state,
                success=True,
                message=None,
            )

        try:
            ctr.start()
        except docker.errors.APIError as e:
            raise ContainerStartError(str(e)) from e

        ctr.reload()
        ins = _normalize_inspection(ctr.attrs)
        return RuntimeActionResult(
            container_id=ins.container_id or container_id,
            container_state=ins.container_state,
            success=ins.container_state == "running",
            message=None if ins.container_state == "running" else f"unexpected state after start: {ins.container_state}",
        )

    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.stop_container is not implemented yet")

    def restart_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.restart_container is not implemented yet")

    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.delete_container is not implemented yet")

    def get_container_netns_ref(self, *, container_id: str) -> NetnsRefResult:
        ins = self.inspect_container(container_id=container_id)
        if not ins.exists or not ins.container_id:
            raise NetnsRefError(f"container not found: {container_id!r}")
        if ins.pid is None:
            raise NetnsRefError(
                f"no host PID for container {ins.container_id!r} (is the container running?)",
            )
        ref = f"/proc/{ins.pid}/ns/net"
        return NetnsRefResult(container_id=ins.container_id, pid=ins.pid, netns_ref=ref)
