"""Docker-backed ``RuntimeAdapter`` (``inspect_container`` implemented; other methods pending)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import docker
import docker.errors

from .interfaces import RuntimeAdapter
from .models import ContainerInspectionResult, NetnsRefResult, RuntimeActionResult, RuntimeEnsureResult


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
        image: str,
        cpu_limit: float | None = None,
        memory_limit_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
        ports: Sequence[tuple[int, int]] | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> RuntimeEnsureResult:
        raise NotImplementedError("DockerRuntimeAdapter.ensure_container is not implemented yet")

    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.start_container is not implemented yet")

    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.stop_container is not implemented yet")

    def restart_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.restart_container is not implemented yet")

    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError("DockerRuntimeAdapter.delete_container is not implemented yet")

    def get_container_netns_ref(self, *, container_id: str) -> NetnsRefResult:
        raise NotImplementedError("DockerRuntimeAdapter.get_container_netns_ref is not implemented yet")
