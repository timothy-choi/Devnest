"""
EC2 provisioning and execution-node lifecycle (control plane).

Creates instances via ``run_instances``, tracks ``ExecutionNode`` rows through ``PROVISIONING`` →
``READY`` (after SSM eligibility for ``ssm_docker``), and supports drain / deregister / terminate.

**Worker IAM (least-privilege sketch — tighten Resource/Condition in production):**

- **EC2:** ``ec2:RunInstances``, ``ec2:CreateTags`` (on instances created by the principal), ``ec2:DescribeInstances``,
  ``ec2:DescribeInstanceTypes``, ``ec2:TerminateInstances`` — scope ``Resource`` to tagged instances (e.g.
  ``aws:ResourceTag/devnest:managed=true``) where your org supports it; ``RunInstances`` often needs broader
  ``subnet``, ``security-group``, ``image``, ``iam:PassRole`` on the instance profile.
- **SSM (readiness only):** ``ssm:DescribeInstanceInformation`` for :func:`~app.services.infrastructure_service.ssm_readiness.is_instance_ssm_online`.
- **Secrets:** Prefer instance role / IRSA / OIDC over long-lived keys; never commit ``AWS_SECRET_ACCESS_KEY``.

TODO: async provisioning jobs, richer bootstrap (cloud-init), multi-instance batches, ASG integration.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.client import BaseClient
from botocore.exceptions import ClientError, WaiterError
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent, log_event
from app.services.node_execution_service.ssm_send_command import build_ssm_client
from app.services.placement_service import get_node
from app.services.placement_service.constants import (
    DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB,
    DEFAULT_EXECUTION_NODE_MAX_WORKSPACES,
)
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.providers.aws_throttle import client_call_with_throttle_retry
from app.services.providers.ec2_provider import (
    EC2_CLIENT_AUTH_ERROR_CODES,
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

    try:
        resp = client_call_with_throttle_retry(
            "ec2.RunInstances",
            lambda: client.run_instances(**_run_instances_params(req, settings)),
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        logger.error(
            "ec2_run_instances_failed",
            extra={
                "error_code": code,
                "instance_type": req.instance_type,
                "region": region,
            },
        )
        if code in EC2_CLIENT_AUTH_ERROR_CODES:
            raise Ec2ProviderError(
                f"AWS denied ec2 RunInstances ({code}); check worker IAM and limits: {e}",
            ) from e
        raise Ec2ProvisionConfigurationError(f"ec2 RunInstances failed ({code}): {e}") from e

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
        except WaiterError as e:
            logger.error(
                "ec2_provision_wait_running_failed_orphan_risk",
                extra={
                    "instance_id": iid,
                    "region": region,
                    "error": str(e),
                    "detail": "EC2 instance may exist without a DevNest execution_node row; terminate in console if abandoned.",
                },
            )
            raise Ec2ProvisionConfigurationError(f"wait instance_running failed for {iid}: {e}") from e
        except ClientError as e:
            logger.error(
                "ec2_provision_wait_running_failed_orphan_risk",
                extra={"instance_id": iid, "region": region, "error": str(e)},
            )
            raise Ec2ProvisionConfigurationError(f"wait instance_running failed for {iid}: {e}") from e

    node_key = explicit_key or f"ec2-{iid}"
    if not explicit_key:
        _require_free_node_key(session, node_key)

    prefix = (settings.devnest_ec2_tag_prefix or "devnest").strip() or "devnest"
    post_tags = [{"Key": f"{prefix}:node_key"[:127], "Value": node_key[:256]}]
    if not (req.name_tag or "").strip():
        post_tags.append({"Key": "Name", "Value": node_key[:256]})
    try:
        client_call_with_throttle_retry(
            "ec2.CreateTags",
            lambda: client.create_tags(Resources=[iid], Tags=post_tags),
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        logger.warning(
            "ec2_create_tags_failed",
            extra={"instance_id": iid, "error_code": code, "error": str(e)},
        )

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

    log_event(
        logger,
        LogEvent.EC2_NODE_PROVISIONED,
        node_key=node_key,
        instance_id=iid,
        provision_correlation_id=corr,
    )
    logger.info(
        "ec2_node_provisioned",
        extra={"node_key": node_key, "instance_id": iid, "provision_correlation_id": corr},
    )
    return row


def _run_instances_params(req: Ec2ProvisionRequest, settings: Any) -> dict[str, Any]:
    prefix = (settings.devnest_ec2_tag_prefix or "devnest").strip() or "devnest"
    explicit_key = (req.node_key or "").strip()
    name = (req.name_tag or "").strip() or (explicit_key if explicit_key else "devnest-node")
    tags: list[dict[str, str]] = [
        {"Key": f"{prefix}:managed"[:127], "Value": "true"},
        {"Key": "Name", "Value": name[:256]},
    ]
    if explicit_key:
        tags.append({"Key": f"{prefix}:node_key"[:127], "Value": explicit_key[:256]})
    # EC2 tag limits: key max 127 chars, value max 256 (UTF-8).
    for tk, tv in (req.extra_tags or {}).items():
        k = str(tk).strip()
        v = str(tv).strip()
        if k and v:
            tags.append({"Key": k[:127], "Value": v[:256]})

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
    user_data = (req.user_data or "").strip()
    if user_data:
        params["UserData"] = user_data
    return params


def register_existing_ec2_node(
    session: Session,
    instance_id: str,
    *,
    ec2_client: BaseClient | None = None,
    node_key: str | None = None,
    ssh_user: str | None = None,
    execution_mode: str | None = None,
    catalog_only: bool = False,
) -> ExecutionNode:
    """Register a pre-existing EC2 instance (delegates to :func:`register_ec2_instance`)."""
    return register_ec2_instance(
        session,
        instance_id,
        ec2_client=ec2_client,
        node_key=node_key,
        ssh_user=ssh_user,
        execution_mode=execution_mode,
        catalog_only=catalog_only,
    )


def register_catalog_ec2_stub(
    session: Session,
    *,
    node_key: str,
    name: str | None = None,
    provider_instance_id: str | None = None,
    private_ip: str | None = None,
    public_ip: str | None = None,
    region: str | None = None,
    availability_zone: str | None = None,
    instance_type: str | None = None,
    execution_mode: str | None = None,
    ssh_user: str | None = None,
    status: str | None = None,
    total_cpu: float | None = None,
    total_memory_mb: int | None = None,
    allocatable_cpu: float | None = None,
    allocatable_memory_mb: int | None = None,
    max_workspaces: int | None = None,
    allocatable_disk_mb: int | None = None,
    align_status_with_heartbeat: bool = False,
) -> ExecutionNode:
    """
    Insert or update an **EC2** execution-node catalog row **without** calling AWS (Phase 3b Step 4).

    Always sets ``schedulable=False`` so the scheduler never selects this node. Does not register
    Traefik routes or change placement predicates.

    Use :func:`register_existing_ec2_node` with ``catalog_only=True`` when you have a real instance id
    and want fields hydrated from ``describe_instances``.
    """
    nk = (node_key or "").strip()
    if not nk:
        raise NodeLifecycleError("node_key is required")

    settings = get_settings()
    default_em = settings.devnest_ec2_default_execution_mode.strip().lower()
    raw_em = (execution_mode or default_em).strip().lower()
    allowed_em = (
        ExecutionNodeExecutionMode.SSH_DOCKER.value,
        ExecutionNodeExecutionMode.SSM_DOCKER.value,
    )
    if raw_em not in allowed_em:
        raw_em = default_em if default_em in allowed_em else ExecutionNodeExecutionMode.SSM_DOCKER.value

    user = (ssh_user or "").strip() or settings.devnest_ec2_ssh_user_default.strip() or "ubuntu"
    reg = (region or (settings.aws_region or "").strip() or "us-east-1").strip() or "us-east-1"
    pid = (provider_instance_id or "").strip() or f"catalog-pending:{nk}"
    if len(pid) > 255:
        raise NodeLifecycleError("provider_instance_id exceeds 255 characters")

    vcpu = float(total_cpu) if total_cpu is not None and float(total_cpu) > 0 else 4.0
    mem = int(total_memory_mb) if total_memory_mb is not None and int(total_memory_mb) > 0 else 8192
    acpu = float(allocatable_cpu) if allocatable_cpu is not None and float(allocatable_cpu) > 0 else vcpu
    amem = int(allocatable_memory_mb) if allocatable_memory_mb is not None and int(allocatable_memory_mb) > 0 else mem
    mxw = int(max_workspaces) if max_workspaces is not None and int(max_workspaces) > 0 else int(
        DEFAULT_EXECUTION_NODE_MAX_WORKSPACES,
    )
    adisk = (
        int(allocatable_disk_mb)
        if allocatable_disk_mb is not None and int(allocatable_disk_mb) > 0
        else int(DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB)
    )

    st_in = (status or "").strip().upper() or None
    if st_in is not None and st_in not in (
        ExecutionNodeStatus.READY.value,
        ExecutionNodeStatus.NOT_READY.value,
    ):
        raise NodeLifecycleError(
            "catalog stub status must be READY or NOT_READY "
            f"(got {st_in!r}); omit status for default NOT_READY",
        )

    now = _now()
    stmt = select(ExecutionNode).where(ExecutionNode.node_key == nk)
    row = session.exec(stmt).first()

    def _resolve_status(r: ExecutionNode | None) -> str:
        if align_status_with_heartbeat:
            hb = r.last_heartbeat_at if r is not None else None
            if hb is None:
                return ExecutionNodeStatus.NOT_READY.value
            s = get_settings()
            max_age = int(s.devnest_node_heartbeat_max_age_seconds or 300)
            age = (now - hb).total_seconds()
            if age >= 0 and age <= max_age:
                return ExecutionNodeStatus.READY.value
            return ExecutionNodeStatus.NOT_READY.value
        if st_in is not None:
            return st_in
        if r is not None and (r.status or "").strip():
            return str(r.status).strip()
        return ExecutionNodeStatus.NOT_READY.value

    meta_patch = {"catalog_ec2_stub": {"updated_at": now.isoformat()}}

    if row is not None:
        if row.provider_type == ExecutionNodeProviderType.LOCAL.value:
            raise NodeLifecycleError(
                f"node_key {nk!r} is already a local execution node; choose another key or remove the row",
            )
        row.name = (name or "").strip() or row.name or nk
        row.provider_type = ExecutionNodeProviderType.EC2.value
        row.provider_instance_id = pid
        row.region = reg
        row.availability_zone = (availability_zone or "").strip() or row.availability_zone
        row.instance_type = (instance_type or "").strip() or row.instance_type
        row.private_ip = (private_ip or "").strip() or row.private_ip
        row.public_ip = (public_ip or "").strip() or row.public_ip
        row.execution_mode = raw_em
        row.ssh_user = user
        row.total_cpu = vcpu
        row.total_memory_mb = mem
        row.allocatable_cpu = acpu
        row.allocatable_memory_mb = amem
        row.max_workspaces = mxw
        row.allocatable_disk_mb = adisk
        row.schedulable = False
        row.status = _resolve_status(row)
        row.updated_at = now
        merged = dict(row.metadata_json or {})
        inner = dict(merged.get("catalog_ec2_stub") or {})
        inner.update(meta_patch["catalog_ec2_stub"])
        merged["catalog_ec2_stub"] = inner
        row.metadata_json = merged
        session.add(row)
        session.flush()
        logger.info(
            "execution_node_catalog_ec2_stub_upserted",
            extra={"node_key": nk, "node_id": row.id, "status": row.status},
        )
        return row

    st_new = _resolve_status(None)
    row = ExecutionNode(
        node_key=nk,
        name=(name or "").strip() or nk,
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=pid,
        region=reg,
        availability_zone=(availability_zone or "").strip() or None,
        instance_type=(instance_type or "").strip() or None,
        hostname=None,
        private_ip=(private_ip or "").strip() or None,
        public_ip=(public_ip or "").strip() or None,
        execution_mode=raw_em,
        ssh_host=None,
        ssh_port=22,
        ssh_user=user,
        status=st_new,
        schedulable=False,
        total_cpu=vcpu,
        total_memory_mb=mem,
        allocatable_cpu=acpu,
        allocatable_memory_mb=amem,
        max_workspaces=mxw,
        allocatable_disk_mb=adisk,
        metadata_json=meta_patch,
        last_synced_at=None,
    )
    session.add(row)
    session.flush()
    logger.info(
        "execution_node_catalog_ec2_stub_inserted",
        extra={"node_key": nk, "node_id": row.id, "status": row.status},
    )
    return row


def mark_node_draining(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
) -> ExecutionNode:
    """Exclude a node from placement (``DRAINING``, ``schedulable=False``)."""
    row = get_node(session, node_id=node_id, node_key=node_key)
    if row.status == ExecutionNodeStatus.TERMINATED.value:
        logger.info(
            "node_drain_skipped_terminated",
            extra={"node_key": row.node_key, "node_id": row.id},
        )
        return row
    if row.status == ExecutionNodeStatus.DRAINING.value and not row.schedulable:
        return row
    row.status = ExecutionNodeStatus.DRAINING.value
    row.schedulable = False
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(row, {"lifecycle": {"draining_marked_at": _now().isoformat()}})
    session.add(row)
    session.flush()
    logger.info(
        "execution_node_draining",
        extra={
            "node_key": row.node_key,
            "node_id": row.id,
            "provider_type": row.provider_type,
            "provider_instance_id": (row.provider_instance_id or "").strip() or None,
        },
    )
    return row


def undrain_node(
    session: Session,
    *,
    node_id: int | None = None,
    node_key: str | None = None,
) -> ExecutionNode:
    """Re-admit a node for placement after drain or catalog-only ``schedulable=false``.

    - **DRAINING** → ``READY`` + ``schedulable=True``.
    - **READY** with ``schedulable=False`` → ``schedulable=True`` (status unchanged).
    - **TERMINATED** / **PROVISIONING** / **NOT_READY** (etc.) → :class:`NodeLifecycleError` — use
      ``POST /internal/execution-nodes/sync`` or ``register-existing`` / provisioning flows instead.

    Idempotent when already ``READY`` and ``schedulable=True``.
    """
    row = get_node(session, node_id=node_id, node_key=node_key)
    if row.status == ExecutionNodeStatus.TERMINATED.value:
        raise NodeLifecycleError(
            f"cannot undrain TERMINATED node (node_key={row.node_key!r}); re-register the instance or restore from backup",
        )
    if row.status == ExecutionNodeStatus.READY.value and bool(row.schedulable):
        return row
    if row.status == ExecutionNodeStatus.DRAINING.value:
        row.status = ExecutionNodeStatus.READY.value
        row.schedulable = True
    elif row.status == ExecutionNodeStatus.READY.value and not bool(row.schedulable):
        row.schedulable = True
    else:
        raise NodeLifecycleError(
            f"undrain supports DRAINING or READY+schedulable=false (node_key={row.node_key!r}, "
            f"status={row.status!r}); for NOT_READY/PROVISIONING use POST /internal/execution-nodes/sync",
        )
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(
        row,
        {"lifecycle": {"undrained_at": _now().isoformat()}},
    )
    session.add(row)
    session.flush()
    logger.info(
        "execution_node_undrained",
        extra={
            "node_key": row.node_key,
            "node_id": row.id,
            "status": row.status,
            "schedulable": bool(row.schedulable),
        },
    )
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
    if row.status == ExecutionNodeStatus.TERMINATED.value and not row.schedulable:
        return row
    row.status = ExecutionNodeStatus.TERMINATED.value
    row.schedulable = False
    row.updated_at = _now()
    row.metadata_json = _merge_metadata(
        row,
        {"lifecycle": {"deregistered_at": _now().isoformat(), "control_plane_inactive": True}},
    )
    session.add(row)
    session.flush()
    logger.info(
        "execution_node_deregistered",
        extra={
            "node_key": row.node_key,
            "node_id": row.id,
            "provider_type": row.provider_type,
            "provider_instance_id": (row.provider_instance_id or "").strip() or None,
        },
    )
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
    if row.status == ExecutionNodeStatus.TERMINATED.value:
        logger.info(
            "ec2_terminate_noop_already_terminated",
            extra={"node_key": row.node_key, "instance_id": (row.provider_instance_id or "").strip()},
        )
        return row
    iid = (row.provider_instance_id or "").strip()
    if not iid:
        raise NodeLifecycleError(f"node {row.node_key!r} has no provider_instance_id")

    if row.status != ExecutionNodeStatus.TERMINATING.value:
        row.status = ExecutionNodeStatus.TERMINATING.value
        row.schedulable = False
        row.updated_at = _now()
        row.metadata_json = _merge_metadata(row, {"lifecycle": {"terminate_requested_at": _now().isoformat()}})
        session.add(row)
        session.flush()
        logger.info(
            "ec2_terminate_requested",
            extra={"node_key": row.node_key, "instance_id": iid},
        )
    else:
        logger.info(
            "ec2_terminate_retry",
            extra={"node_key": row.node_key, "instance_id": iid, "detail": "already TERMINATING; re-invoking AWS"},
        )

    client = ec2_client or build_ec2_client(region=(row.region or "").strip() or None)
    try:
        client_call_with_throttle_retry(
            "ec2.TerminateInstances",
            lambda: client.terminate_instances(InstanceIds=[iid]),
        )
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
        except WaiterError as e:
            logger.warning(
                "ec2_wait_instance_terminated_timeout",
                extra={"instance_id": iid, "error": str(e)},
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
    log_event(
        logger,
        LogEvent.EC2_NODE_TERMINATED,
        node_key=row.node_key,
        instance_id=iid,
        execution_node_status=row.status,
        ec2_state=state,
    )
    logger.info(
        "ec2_terminate_reconciled",
        extra={
            "node_key": row.node_key,
            "instance_id": iid,
            "status": row.status,
            "ec2_state": state,
        },
    )
    return row


def _provisioning_heartbeat_reports_docker_ready(row: ExecutionNode) -> bool:
    """Require a fresh node heartbeat with ``docker_ok=true`` before admitting EC2 capacity."""
    hb = row.last_heartbeat_at
    if hb is None:
        return False
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    max_age = int(get_settings().devnest_node_heartbeat_max_age_seconds or 300)
    age = (_now() - hb).total_seconds()
    if age < 0 or age > max_age:
        return False
    heartbeat = dict((row.metadata_json or {}).get("heartbeat") or {})
    return heartbeat.get("docker_ok") is True


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
    ``READY`` when the instance is running, the control transport is available, and a fresh heartbeat
    has reported ``docker_ok=true``.
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

    if row.status == ExecutionNodeStatus.ERROR.value:
        logger.warning(
            "ec2_sync_node_in_error",
            extra={
                "node_key": row.node_key,
                "instance_id": iid,
                "last_error_code": row.last_error_code,
            },
        )

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

    if not _provisioning_heartbeat_reports_docker_ready(row):
        logger.info(
            "ec2_node_provisioning_waiting_heartbeat_docker",
            extra={"node_key": row.node_key, "instance_id": iid},
        )
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
    readiness = "ssm" if mode == ExecutionNodeExecutionMode.SSM_DOCKER.value else "ssh_docker_running"
    row.metadata_json = _merge_metadata(
        row,
        {"lifecycle": {"ready_at": _now().isoformat(), "readiness": readiness}},
    )
    session.add(row)
    session.flush()
    logger.info(
        "ec2_node_ready",
        extra={"node_key": row.node_key, "instance_id": iid, "execution_mode": row.execution_mode},
    )
    return row
