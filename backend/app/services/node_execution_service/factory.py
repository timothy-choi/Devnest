"""Build :class:`NodeExecutionBundle` from ``ExecutionNode`` rows (placement output)."""

from __future__ import annotations

from urllib.parse import quote

import docker
from sqlmodel import Session, select

from app.libs.topology.system.command_runner import CommandRunner

from app.services.placement_service.models import ExecutionNode, ExecutionNodeExecutionMode

from .bundle import NodeExecutionBundle
from .errors import NodeExecutionBindingError
from .ssh_command_runner import SshRemoteCommandRunner
from .workspace_project_dir import (
    default_local_ensure_workspace_project_dir,
    ssh_remote_ensure_workspace_project_dir,
)


def _execution_connect_host(node: ExecutionNode) -> str:
    """Resolve SSH/Docker host from node row (explicit ssh_host preferred; EC2 often uses private_ip)."""
    for candidate in (node.ssh_host, node.hostname, node.private_ip):
        s = (candidate or "").strip()
        if s:
            return s
    return ""


def resolve_node_execution_bundle(
    session: Session,
    execution_node_key: str | None,
) -> NodeExecutionBundle:
    """
    Resolve Docker client + command runners for ``execution_node_key``.

    - **Missing / empty key:** legacy single-host dev — ``docker.from_env()``, local
      :class:`~app.libs.topology.system.command_runner.CommandRunner`, local project dirs, local TCP
      probes. No ``ExecutionNode`` row is read.
    - **Key with DB row:** honor normalized ``execution_mode`` (``local_docker`` | ``ssh_docker``).
      ``LOCAL_DOCKER`` always uses the worker's local engine (``docker.from_env()``); ``ssh_*`` /
      ``hostname`` / ``private_ip`` on the row are ignored for that mode.
    - **Key without row:** :class:`NodeExecutionBindingError` (do not silently misplace workloads).
    """
    key = (execution_node_key or "").strip()
    if not key:
        return _bundle_local_docker()

    row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if row is None:
        raise NodeExecutionBindingError(
            f"no execution_node row for node_key={key!r}; fix bootstrap/placement or WorkspaceRuntime",
        )

    raw_mode = row.execution_mode or ExecutionNodeExecutionMode.LOCAL_DOCKER.value
    mode = str(raw_mode).strip().lower()
    if mode == ExecutionNodeExecutionMode.SSH_DOCKER.value:
        return _bundle_ssh_docker(row)
    if mode not in (
        ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
        "",
    ):
        raise NodeExecutionBindingError(
            f"unsupported execution_mode {raw_mode!r} for node_key={key!r}",
        )
    return _bundle_local_docker()


def _bundle_local_docker() -> NodeExecutionBundle:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        raise NodeExecutionBindingError(
            f"Docker engine not available for local execution: {e}",
        ) from e
    runner = CommandRunner()
    return NodeExecutionBundle(
        docker_client=client,
        topology_command_runner=runner,
        service_reachability_runner=None,
        _ensure_project_dir=default_local_ensure_workspace_project_dir,
    )


def _bundle_ssh_docker(node: ExecutionNode) -> NodeExecutionBundle:
    host = _execution_connect_host(node)
    if not host:
        raise NodeExecutionBindingError(
            f"ssh_docker node {node.node_key!r} requires a non-empty ssh_host, hostname, or private_ip",
        )
    user = (node.ssh_user or "").strip() or "root"
    port = int(node.ssh_port or 22)
    try:
        import paramiko  # noqa: F401 — docker-py requires paramiko for ssh:// URLs
    except ImportError as e:
        raise NodeExecutionBindingError(
            "execution_mode ssh_docker requires the paramiko package (install paramiko).",
        ) from e

    user_enc = quote(user, safe="")
    base_url = f"ssh://{user_enc}@{host}:{port}"
    try:
        client = docker.DockerClient(base_url=base_url)
        client.ping()
    except Exception as e:
        raise NodeExecutionBindingError(
            f"cannot reach Docker daemon on node {node.node_key!r} via {base_url!r}: {e}",
        ) from e

    ssh_runner = SshRemoteCommandRunner(ssh_user=user, ssh_host=host, ssh_port=port)

    def _ensure(base: str, wid: str) -> str:
        return ssh_remote_ensure_workspace_project_dir(ssh_runner, base, wid)

    return NodeExecutionBundle(
        docker_client=client,
        topology_command_runner=ssh_runner,
        service_reachability_runner=ssh_runner,
        _ensure_project_dir=_ensure,
    )
