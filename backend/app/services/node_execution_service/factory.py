"""Build :class:`NodeExecutionBundle` from ``ExecutionNode`` rows (placement output)."""

from __future__ import annotations

import os
from urllib.parse import quote

import docker
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.runtime.ssm_docker_runtime import SsmDockerRuntimeAdapter
from app.libs.topology.system.command_runner import CommandRunner
from app.libs.topology.system.host_nsenter_command_runner import HostPid1NsenterRunner

from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
)

from .bundle import NodeExecutionBundle
from .errors import NodeExecutionBindingError
from .ssh_command_runner import SshRemoteCommandRunner
from .ssm_remote_command_runner import SsmRemoteCommandRunner
from .workspace_project_dir import (
    default_local_ensure_workspace_project_dir,
    remote_shell_ensure_workspace_project_dir,
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
    Resolve Docker client and/or SSM runtime + command runners for ``execution_node_key``.

    - **Missing / empty key:** legacy single-host dev — local Docker + local ``CommandRunner``.
    - **Key with DB row:** ``execution_mode`` selects ``local_docker`` (worker engine), ``ssh_docker``
      (docker-py over SSH), or ``ssm_docker`` (Docker CLI via SSM Run Command on the instance).
    - **``DEVNEST_EXECUTION_MODE``:** optional override — ``local`` forces worker-local Docker for
      ``provider_type=local`` rows only; ``ssm`` forces SSM for ``provider_type=ec2`` rows only.
    - **Key without row:** :class:`NodeExecutionBindingError`.
    """
    key = (execution_node_key or "").strip()
    if not key:
        override = (get_settings().devnest_execution_mode or "").strip().lower()
        if override == "ssm":
            raise NodeExecutionBindingError(
                "DEVNEST_EXECUTION_MODE=ssm requires a non-empty execution_node_key (placement context)",
            )
        return _bundle_local_docker()

    row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == key)).first()
    if row is None:
        raise NodeExecutionBindingError(
            f"no execution_node row for node_key={key!r}; fix bootstrap/placement or WorkspaceRuntime",
        )

    exec_override = (get_settings().devnest_execution_mode or "").strip().lower()
    if exec_override == "local":
        if row.provider_type != ExecutionNodeProviderType.LOCAL.value:
            raise NodeExecutionBindingError(
                "DEVNEST_EXECUTION_MODE=local is only valid for provider_type=local nodes "
                f"(node_key={key!r} has provider_type={row.provider_type!r})",
            )
        return _bundle_local_docker()
    if exec_override == "ssm":
        if row.provider_type != ExecutionNodeProviderType.EC2.value:
            raise NodeExecutionBindingError(
                "DEVNEST_EXECUTION_MODE=ssm requires provider_type=ec2 "
                f"(node_key={key!r} has provider_type={row.provider_type!r})",
            )
        return _bundle_ssm_docker(row)

    raw_mode = row.execution_mode or ExecutionNodeExecutionMode.LOCAL_DOCKER.value
    mode = str(raw_mode).strip().lower()
    if mode == ExecutionNodeExecutionMode.SSM_DOCKER.value:
        return _bundle_ssm_docker(row)
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


def _topology_ip_should_use_host_nsenter() -> bool:
    """When true, topology ``ip`` runs under ``nsenter -t 1 …`` (host init net/pid/mount view)."""
    return os.environ.get("DEVNEST_TOPOLOGY_IP_VIA_HOST_NSENTER", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _bundle_local_docker() -> NodeExecutionBundle:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        raise NodeExecutionBindingError(
            f"Docker engine not available for local execution: {e}",
        ) from e
    base_runner = CommandRunner()
    runner: CommandRunner = (
        HostPid1NsenterRunner(base_runner) if _topology_ip_should_use_host_nsenter() else base_runner
    )
    return NodeExecutionBundle(
        docker_client=client,
        topology_command_runner=runner,
        service_reachability_runner=None,
        _ensure_project_dir=default_local_ensure_workspace_project_dir,
        runtime_adapter=None,
    )


def _bundle_ssm_docker(node: ExecutionNode) -> NodeExecutionBundle:
    """
    Docker + Linux commands on the instance via SSM (no SSH keys).

    Requires ``provider_instance_id`` (EC2 id registered with SSM) and ``region`` (row or ``AWS_REGION``).
    """
    iid = (node.provider_instance_id or "").strip()
    if not iid:
        raise NodeExecutionBindingError(
            f"ssm_docker node {node.node_key!r} requires provider_instance_id (EC2 instance id for SSM)",
        )
    region = (node.region or "").strip() or (get_settings().aws_region or "").strip()
    if not region:
        raise NodeExecutionBindingError(
            f"ssm_docker node {node.node_key!r} requires region on the row or AWS_REGION in the environment",
        )

    ssm_runner = SsmRemoteCommandRunner(instance_id=iid, region=region)
    try:
        ssm_runner.run(["echo", "devnest-ssm-ping"])
    except RuntimeError as e:
        raise NodeExecutionBindingError(
            f"SSM connectivity check failed for node {node.node_key!r} (instance {iid!r}): {e}",
        ) from e

    runtime = SsmDockerRuntimeAdapter(ssm_runner)

    def _ensure(base: str, wid: str) -> str:
        return remote_shell_ensure_workspace_project_dir(ssm_runner, base, wid)

    return NodeExecutionBundle(
        docker_client=None,
        topology_command_runner=ssm_runner,
        service_reachability_runner=ssm_runner,
        _ensure_project_dir=_ensure,
        runtime_adapter=runtime,
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
        runtime_adapter=None,
    )
