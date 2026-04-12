"""
EC2 provisioning and execution-node lifecycle (control plane).

Creates instances via ``run_instances``, tracks ``ExecutionNode`` rows through ``PROVISIONING`` →
``READY`` (after SSM eligibility for ``ssm_docker``), and supports drain / deregister / terminate.

TODO: async provisioning jobs, richer bootstrap (cloud-init), multi-instance batches, ASG integration.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.node_execution_service.ssm_send_command import build_ssm_client
from app.services.placement_service import get_node
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.providers.ec2_provider import (
    build_ec2_client,
    describe_ec2_instance,
    ec2_instance_type_capacity,
    register_ec2_instance,
)
from app.services.providers.errors import Ec2InstanceNotFoundError, Ec2ProviderError

from .errors import Ec2ProvisionConfigurationError, NodeLifecycleError
from .models import Ec2ProvisionRequest
from .ssm_readiness import is_instance_ssm_online

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _merge_metadata(row: ExecutionNode, patch: dict[str, Any]) -> dict[str, Any]:
    base = dict(row.metadata_json or {})
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            inner = dict(base[k])
            inner.update(v)
            base[k] = inner
        else:
            base[k] = v
    return base


def _assert_ec2_node(row: ExecutionNode, *, op: str) -> None:
    if row.provider_type != ExecutionNodeProviderType.EC2.value:
        raise NodeLifecycleError(f"{op} requires provider_type=ec2 (node_key={row.node_key!r})")


def _require_free_node_key(session: Session, node_key: str) -> None:
    stmt = select(ExecutionNode).where(ExecutionNode.node_key == node_key)
    if session.exec(stmt).first() is not None:
        raise NodeLifecycleError(f"node_key {node_key!r} already exists")


def provision_ec2_node(
    session: Session,
    request: Ec2ProvisionRequest | None = None,
    *,
    ec2_client: BaseClient | None = None,
    wait_until_running: bool = True,
) -> ExecutionNode:
    """
    Launch one EC2 instance and insert a ``PROVISIONING`` :class:`ExecutionNode`.

    Tags: ``{tag_prefix}:managed``, ``{tag_prefix}:node_key``, ``Name`` (and optional ``extra_tags``).

    Caller must ``commit``. Does not verify SSM — use :func:`sync_node_state` after the instance runs.
    """
    settings = get_settings()
    req = request or Ec2ProvisionRequest.from_settings(settings)
    req.validate()

    explicit_key = (req.node_key or "").strip()
    if explicit_key:
        _require_free_node_key(session, explicit_key)

    client = ec2_client or build_ec2_client(region=req.region)
    region = client.meta.region_name or (req.region or settings.aws_region or "").strip() or "unknown"

    resp = client.run_instances(**_run_instances_params(req, settings))
    instances = resp.get("Instances") or []
    if not instances:
        raise Ec2ProvisionConfigurationError("run_instances returned no Instances")
    iid = (instances[0].get("InstanceId") or "").strip()
    if not iid:
        raise Ec2ProvisionConfigurationError("run_instances returned empty InstanceId")

    if wait_until_running:
        try:
            client.get_waiter("instance_running").wait(
                InstanceIds=[iid],
                WaiterConfig={"Delay": 5, "MaxAttempts": 60},
            )
        except ClientError as e:
            raise Ec2ProvisionConfigurationError(f"wait instance_running failed for {iid}: {e}") from e

    node_key = explicit_key or f"ec2-{iid}"
    if not explicit_key:
        _require_free_node_key(session, node_key)

    prefix = (settings.devnest_ec2_tag_prefix or "devnest").strip() or "devnest"
    post_tags = [{"Key": f"{prefix}:node_key", "Value": node_key[:255]}]
    if not (req.name_tag or "").strip():
        post_tags.append({"Key": "Name", "Value": node_key[:255]})
    try:
        client.create_tags(Resources=[iid], Tags=post_tags)
    except ClientError as e:
        logger.warning("ec2_create_tags_failed", extra={"instance_id": iid, "error": str(e)})

    default_em = settings.devnest_ec2_default_execution_mode.strip().lower()
    raw_em = (req.execution_mode or default_em).strip().lower()
    allowed = (ExecutionNodeExecutionMode.SSH_DOCKER.value, ExecutionNodeExecutionMode.SSM_DOCKER.value)
    if raw_em not in allowed:
        raw_em = default_em if default_em in allowed else ExecutionNodeExecutionMode.SSM_DOCKER.value
    user = (req.ssh_user or "").strip() or settings.devnest_ec2_ssh_user_default.strip() or "ubuntu"

    vcpu, mem_mb = ec2_instance_type_capacity(client, req.instance_type)
    corr = uuid.uuid4().hex[:16]
    now = _now()
    meta = {
        "ec2": {
            "provisioned_at": now.isoformat(),
            "provision_correlation_id": corr,
            "managed": True,
            "region": region,
        },
    }
    row = ExecutionNode(
        node_key=node_key,
        name=(req.name_tag or "").strip() or node_key,
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=iid,
        region=region,
        availability_zone=None,
        instance_type=req.instance_type,
        hostname=None,
        private_ip=None,
        public_ip=None,
        execution_mode=raw_em,
        ssh_host=None,
        ssh_port=22,
        ssh_user=user,
        status=ExecutionNodeStatus.PROVISIONING.value,
        schedulable=False,
        total_cpu=vcpu,
        total_memory_mb=mem_mb,
        allocatable_cpu=vcpu,
        allocatable_memory_mb=mem_mb,
        metadata_json=meta,
        iam_instance_profile_name=(req.iam_instance_profile_name or "").strip() or None,
        last_synced_at=None,
    )
    session.add(row)
    session.flush()

    logger.info(
        "ec2_node_provisioned",
        extra={"node_key": node_key, "instance_id": iid, "correlation_id": corr},
    )
    return row


def _run_instances_params(req: Ec2ProvisionRequest, settings: Any) -> dict[str, Any]:
    prefix = (settings.devnest_ec2_tag_prefix or "devnest").strip() or "devnest"
    explicit_key = (req.node_key or "").strip()
    name = (req.name_tag or "").strip() or (explicit_key if explicit_key else "devnest-node")
    tags: list[dict[str, str]] = [
        {"Key": f"{prefix}:managed", "Value": "true"},
        {"Key": "Name", "Value": name[:255]},
    ]
    if explicit_key:
        tags.append({"Key": f"{prefix}:node_key", "Value": explicit_key[:255]})
    for tk, tv in (req.extra_tags or {}).items():
        k = str(tk).strip()
        v = str(tv).strip()
        if k and v:
            tags.append({"Key": k[:255], "Value": v[:255]})

    params: dict[str, Any] = {
        "ImageId": req.ami_id.strip(),
        "MinCount": 1,
        "MaxCount": 1,
        "InstanceType": req.instance_type.strip(),
        "SubnetId": req.subnet_id.strip(),
        "SecurityGroupIds": list(req.security_group_ids),
        "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}],
    }
    prof = (req.iam_instance_profile_name or "").strip()
    if prof:
        params["IamInstanceProfile"] = {"Name": prof}
    key = (req.key_name or "").strip()
    if key:
        params["KeyName"] = key
    return params


def register_existing_ec2_node(
    session: Session,
    instance_id: str,
    *,
    ec2_client: BaseClient | None = None,
    node_key: str | None = None,
    ssh_user: str | None = None,
    execution_mode: str | None = None,
) -> ExecutionNode:
    """Register a pre-existing EC2 instance (delegates to :func:`register_ec2_instance`)."""
    return register_ec2_instance(
        session,
        instance_id,
        ec2_client=ec2_client,
        node_key=node_key,
        ssh_user=ssh_user,
        execution_mode=execution_mode,
    )


def mark_node_draining(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
) -> ExecutionNode:
    """Exclude a node from placement (``DRAINING``, ``schedulable=False``)."""
    row = get_node(session, node_id=node_id, node_key=node_key)
    row.status = ExecutionNodeStatus.DRAINING.value
    row.schedulable = False
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(row, {"lifecycle": {"draining_marked_at": _now().isoformat()}})
    session.add(row)
    session.flush()
    return row


def deregister_node(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
) -> ExecutionNode:
    """
    Soft-remove a node from scheduling: ``TERMINATED`` + ``schedulable=False``.

    Does **not** stop AWS instances; use :func:`terminate_ec2_node` first when appropriate.
    """
    row = get_node(session, node_id=node_id, node_key=node_key)
    row.status = ExecutionNodeStatus.TERMINATED.value
    row.schedulable = False
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(
        row,
        {"lifecycle": {"deregistered_at": _now().isoformat(), "control_plane_inactive": True}},
    )
    session.add(row)
    session.flush()
    return row


def terminate_ec2_node(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
    ec2_client: BaseClient | None = None,
    wait_until_terminated: bool = True,
) -> ExecutionNode:
    """
    Mark ``TERMINATING``, call ``terminate_instances``, then ``TERMINATED`` when AWS state allows.

    Caller must ``commit``.
    """
    row = get_node(session, node_id=node_id, node_key=node_key)
    _assert_ec2_node(row, op="terminate_ec2_node")
    iid = (row.provider_instance_id or "").strip()
    if not iid:
        raise NodeLifecycleError(f"node {row.node_key!r} has no provider_instance_id")

    row.status = ExecutionNodeStatus.TERMINATING.value
    row.schedulable = False
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(row, {"lifecycle": {"terminate_requested_at": _now().isoformat()}})
    session.add(row)
    session.flush()

    client = ec2_client or build_ec2_client(region=(row.region or "").strip() or None)
    try:
        client.terminate_instances(InstanceIds=[iid])
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        row.status = ExecutionNodeStatus.ERROR.value
        row.last_error_code = code[:64] if code else "TerminateFailed"
        row.last_error_message = str(e)[:4096]
        row.updated_at = _now()
        session.add(row)
        session.flush()
        raise Ec2ProviderError(f"terminate_instances failed for {iid}: {e}") from e

    if wait_until_terminated:
        try:
            client.get_waiter("instance_terminated").wait(
                InstanceIds=[iid],
                WaiterConfig={"Delay": 5, "MaxAttempts": 60},
            )
        except ClientError as e:
            logger.warning("ec2_wait_instance_terminated_failed", extra={"instance_id": iid, "error": str(e)})

    try:
        desc = describe_ec2_instance(iid, ec2_client=client)
        state = desc.state
    except Ec2InstanceNotFoundError:
        state = "terminated"
    except Ec2ProviderError:
        state = "unknown"

    if state in ("terminated", "shutting-down"):
        row.status = ExecutionNodeStatus.TERMINATED.value
    else:
        row.status = ExecutionNodeStatus.TERMINATING.value
    row.schedulable = False
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(
        row,
        {"ec2": {"state": state, "synced_at": _now().isoformat()}},
    )
    session.add(row)
    session.flush()
    return row


def sync_node_state(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
    ec2_client: BaseClient | None = None,
    ssm_client: BaseClient | None = None,
    promote_provisioning_when_ready: bool = True,
) -> ExecutionNode:
    """
    Refresh EC2 fields via :func:`register_ec2_instance`, then optionally promote ``PROVISIONING`` →
    ``READY`` when the instance is running and (for ``ssm_docker``) SSM reports the agent online.
    """
    row = get_node(session, node_id=node_id, node_key=node_key)
    _assert_ec2_node(row, op="sync_node_state")
    iid = (row.provider_instance_id or "").strip()
    if not iid:
        raise NodeLifecycleError(f"node {row.node_key!r} has no provider_instance_id")

    client = ec2_client or build_ec2_client(region=(row.region or "").strip() or None)
    register_ec2_instance(
        session,
        iid,
        ec2_client=client,
        node_key=row.node_key,
        ssh_user=row.ssh_user,
        execution_mode=row.execution_mode,
    )
    session.refresh(row)

    if not promote_provisioning_when_ready:
        return row

    if row.status != ExecutionNodeStatus.PROVISIONING.value:
        return row

    try:
        desc = describe_ec2_instance(iid, ec2_client=client)
    except Ec2ProviderError:
        return row

    if desc.state != "running":
        return row

    mode = (row.execution_mode or "").strip().lower()
    if mode == ExecutionNodeExecutionMode.SSM_DOCKER.value:
        ssm = ssm_client or build_ssm_client(region=(row.region or "").strip() or None)
        if not is_instance_ssm_online(ssm, iid):
            logger.info(
                "ec2_node_provisioning_waiting_ssm",
                extra={"node_key": row.node_key, "instance_id": iid},
            )
            return row
    elif mode == ExecutionNodeExecutionMode.SSH_DOCKER.value:
        pass
    else:
        return row

    row.status = ExecutionNodeStatus.READY.value
    row.schedulable = True
    row.last_error_code = None
    row.last_error_message = None
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(
        row,
        {"lifecycle": {"ready_at": _now().isoformat(), "readiness": "ssm" if mode == ExecutionNodeExecutionMode.SSM_DOCKER.value else "ssh_docker_running"}},
    )
    session.add(row)
    session.flush()
    logger.info(
        "ec2_node_ready",
        extra={"node_key": row.node_key, "instance_id": iid, "execution_mode": row.execution_mode},
    )
    return row
