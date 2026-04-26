"""Read-only execution-node connectivity smoke (SSM or SSH), Phase 3b Step 6.

Does not change ``schedulable`` or placement; operators call internal API to verify the control
plane can reach a registered EC2 node's Docker via the same execution path used for workloads.
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session

from app.services.node_execution_service.errors import SsmExecutionError
from app.services.node_execution_service.ssh_command_runner import SshRemoteCommandRunner
from app.services.node_execution_service.ssm_send_command import build_ssm_client, send_run_shell_script
from app.services.placement_service import get_node
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
)

# Read-only, bounded output (no user-controlled command strings).
_SMOKE_SHELL = "docker info 2>&1 | head -c 2000"
_SMOKE_SSM_TIMEOUT_S = 120
_OUTPUT_MAX = 2000


class ExecutionNodeSmokeUnsupportedError(Exception):
    """Raised when ``execution_mode`` / provider cannot use SSM or SSH smoke."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _resolve_ssh_host(node: ExecutionNode) -> str:
    for candidate in (node.ssh_host, node.hostname, node.private_ip):
        s = (candidate or "").strip()
        if s:
            return s
    return ""


def _sanitize_output_preview(text: str) -> str:
    """Truncate and strip control characters; redact obvious long-lived AWS access key ids."""
    raw = (text or "")[:_OUTPUT_MAX]
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    return re.sub(r"\b(AKIA[0-9A-Z]{16})\b", "[REDACTED]", raw)


def run_read_only_execution_node_smoke(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
) -> dict[str, Any]:
    """
    Run a bounded ``docker info`` on the target node via SSM (``ssm_docker``) or SSH (``ssh_docker``).

    Returns a dict suitable for :class:`~app.services.infrastructure_service.api.schemas.ExecutionNodeSmokeResponse`.
    """
    node = get_node(session, node_id=node_id, node_key=node_key)

    if (node.provider_type or "").strip().lower() != ExecutionNodeProviderType.EC2.value:
        raise ExecutionNodeSmokeUnsupportedError(
            f"smoke-read-only supports provider_type=ec2 (got {node.provider_type!r} for node_key={node.node_key!r})",
        )

    mode = (node.execution_mode or "").strip().lower()
    instance_id = (node.provider_instance_id or "").strip()

    if mode == ExecutionNodeExecutionMode.SSM_DOCKER.value:
        if not instance_id:
            return _failure_payload(
                node,
                command_status="Skipped",
                message="missing provider_instance_id for ssm_docker",
            )
        region = (node.region or "").strip()
        if not region:
            return _failure_payload(node, command_status="Skipped", message="missing region for ssm_docker")
        ssm = build_ssm_client(region=region or None)
        try:
            stdout, stderr = send_run_shell_script(
                ssm,
                instance_id,
                [_SMOKE_SHELL],
                comment="DevNest-smoke-readonly",
                timeout_seconds=_SMOKE_SSM_TIMEOUT_S,
            )
        except SsmExecutionError as e:
            return _failure_payload(node, command_status="Failed", message=str(e))
        combined = (stdout or "") + (("\n" + stderr) if stderr else "")
        return _success_payload(node, combined)

    if mode == ExecutionNodeExecutionMode.SSH_DOCKER.value:
        host = _resolve_ssh_host(node)
        if not host:
            return _failure_payload(node, command_status="Skipped", message="no ssh_host/hostname/private_ip for ssh_docker")
        user = (node.ssh_user or "").strip() or "ubuntu"
        port = int(node.ssh_port or 22)
        runner = SshRemoteCommandRunner(ssh_user=user, ssh_host=host, ssh_port=port)
        try:
            out = runner.run(["sh", "-c", _SMOKE_SHELL])
        except Exception as e:  # noqa: BLE001 — return sanitized failure for operators
            return _failure_payload(node, command_status="Failed", message=str(e)[:500])
        return _success_payload(node, out)

    raise ExecutionNodeSmokeUnsupportedError(
        f"smoke-read-only supports execution_mode ssm_docker or ssh_docker (got {node.execution_mode!r})",
    )


def _success_payload(node: ExecutionNode, output: str) -> dict[str, Any]:
    return {
        "ok": True,
        "node_key": node.node_key,
        "execution_mode": str(node.execution_mode or ""),
        "schedulable": bool(node.schedulable),
        "status": str(node.status or ""),
        "command_status": "Success",
        "output_preview": _sanitize_output_preview(output),
        "provider_instance_id": (node.provider_instance_id or "").strip() or None,
    }


def _failure_payload(node: ExecutionNode, *, command_status: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "node_key": node.node_key,
        "execution_mode": str(node.execution_mode or ""),
        "schedulable": bool(node.schedulable),
        "status": str(node.status or ""),
        "command_status": command_status,
        "output_preview": _sanitize_output_preview(message),
        "provider_instance_id": (node.provider_instance_id or "").strip() or None,
    }
