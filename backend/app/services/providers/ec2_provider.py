"""
Register and refresh :class:`~app.services.placement_service.models.ExecutionNode` rows from AWS EC2.

Uses ``describe_instances`` / ``describe_instance_types`` only — **no** ``run_instances``,
autoscaling, or SSM. Operators (or a future provisioner) create instances; DevNest maps them.

SSH keys, security groups, and Docker on the instance are operator concerns (TODO: runbooks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)

from .errors import Ec2InstanceNotFoundError, Ec2ProviderError

_INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,32}$", re.IGNORECASE)


@dataclass(frozen=True)
class Ec2InstanceDescription:
    """Normalized EC2 view for mapping onto :class:`ExecutionNode`."""

    instance_id: str
    state: str
    instance_type: str
    private_ip: str | None
    public_ip: str | None
    availability_zone: str | None
    region: str
    name_tag: str | None
    iam_instance_profile_name: str | None


def build_ec2_client(*, region: str | None = None) -> BaseClient:
    """
    Build a boto3 EC2 client using settings and the standard credential chain.

    When ``aws_access_key_id`` / ``aws_secret_access_key`` are empty, boto3 uses env vars,
    shared config, or instance role.
    """
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    r = (region or settings.aws_region or "").strip()
    if r:
        kwargs["region_name"] = r
    key = (settings.aws_access_key_id or "").strip()
    secret = (settings.aws_secret_access_key or "").strip()
    if key and secret:
        kwargs["aws_access_key_id"] = key
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("ec2", **kwargs)


def _require_instance_id(instance_id: str) -> str:
    iid = (instance_id or "").strip()
    if not iid or not _INSTANCE_ID_RE.match(iid):
        raise Ec2ProviderError(f"invalid EC2 instance id: {instance_id!r}")
    return iid


def _name_from_tags(tags: list[dict[str, str]] | None) -> str | None:
    if not tags:
        return None
    for t in tags:
        if (t or {}).get("Key") == "Name":
            v = (t.get("Value") or "").strip()
            return v or None
    return None


def _profile_name_from_instance(inst: dict[str, Any]) -> str | None:
    prof = inst.get("IamInstanceProfile") or {}
    arn = (prof.get("Arn") or "").strip()
    if not arn:
        return None
    # arn:aws:iam::123456789012:instance-profile/MyProfile
    if "/" in arn:
        return arn.rsplit("/", 1)[-1].strip() or None
    return None


def describe_ec2_instance(
    instance_id: str,
    *,
    ec2_client: BaseClient | None = None,
) -> Ec2InstanceDescription:
    """Return live EC2 fields for ``instance_id`` (raises if not found)."""
    iid = _require_instance_id(instance_id)
    client = ec2_client or build_ec2_client()
    region = client.meta.region_name or (get_settings().aws_region or "").strip() or "unknown"
    try:
        resp = client.describe_instances(InstanceIds=[iid])
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code == "InvalidInstanceID.NotFound":
            raise Ec2InstanceNotFoundError(f"EC2 instance not found: {iid}") from e
        raise Ec2ProviderError(f"describe_instances failed for {iid}: {e}") from e

    reservations = resp.get("Reservations") or []
    if not reservations:
        raise Ec2InstanceNotFoundError(f"EC2 instance not found: {iid}")
    instances = reservations[0].get("Instances") or []
    if not instances:
        raise Ec2InstanceNotFoundError(f"EC2 instance not found: {iid}")
    inst = instances[0]
    state = ((inst.get("State") or {}).get("Name") or "").strip().lower() or "unknown"
    priv = (inst.get("PrivateIpAddress") or "").strip() or None
    pub = (inst.get("PublicIpAddress") or "").strip() or None
    az = ((inst.get("Placement") or {}).get("AvailabilityZone") or "").strip() or None
    itype = (inst.get("InstanceType") or "").strip() or "unknown"
    name = _name_from_tags(inst.get("Tags"))
    profile = _profile_name_from_instance(inst)

    return Ec2InstanceDescription(
        instance_id=iid,
        state=state,
        instance_type=itype,
        private_ip=priv,
        public_ip=pub,
        availability_zone=az,
        region=region,
        name_tag=name,
        iam_instance_profile_name=profile,
    )


def _instance_type_capacity(client: BaseClient, instance_type: str) -> tuple[float, int]:
    """Default vCPU and memory (MiB) from ``describe_instance_types``; fallback if unknown."""
    it = (instance_type or "").strip()
    if not it or it == "unknown":
        return 4.0, 8192
    try:
        resp = client.describe_instance_types(InstanceTypes=[it])
    except ClientError:
        return 4.0, 8192
    types = resp.get("InstanceTypes") or []
    if not types:
        return 4.0, 8192
    row = types[0]
    vcpu = float((row.get("VCpuInfo") or {}).get("DefaultVCpus") or 4)
    mem = int((row.get("MemoryInfo") or {}).get("SizeInMiB") or 8192)
    return max(1.0, vcpu), max(512, mem)


def list_ec2_instances(
    *,
    ec2_client: BaseClient | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> list[Ec2InstanceDescription]:
    """
    List instances in the account/region via paginated ``describe_instances``.

    Default ``filters`` restrict to ``running`` (override for other states). TODO: tag-based
    discovery (e.g. ``devnest:managed``) for large accounts.
    """
    client = ec2_client or build_ec2_client()
    region = client.meta.region_name or (get_settings().aws_region or "").strip() or "unknown"
    flt = filters if filters is not None else [{"Name": "instance-state-name", "Values": ["running"]}]
    out: list[Ec2InstanceDescription] = []
    paginator = client.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=flt):
        for res in page.get("Reservations") or []:
            for inst in res.get("Instances") or []:
                iid = (inst.get("InstanceId") or "").strip()
                if not iid:
                    continue
                state = ((inst.get("State") or {}).get("Name") or "").strip().lower() or "unknown"
                priv = (inst.get("PrivateIpAddress") or "").strip() or None
                pub = (inst.get("PublicIpAddress") or "").strip() or None
                az = ((inst.get("Placement") or {}).get("AvailabilityZone") or "").strip() or None
                itype = (inst.get("InstanceType") or "").strip() or "unknown"
                name = _name_from_tags(inst.get("Tags"))
                profile = _profile_name_from_instance(inst)
                out.append(
                    Ec2InstanceDescription(
                        instance_id=iid,
                        state=state,
                        instance_type=itype,
                        private_ip=priv,
                        public_ip=pub,
                        availability_zone=az,
                        region=region,
                        name_tag=name,
                        iam_instance_profile_name=profile,
                    ),
                )
    return out


def register_ec2_instance(
    session: Session,
    instance_id: str,
    *,
    ec2_client: BaseClient | None = None,
    node_key: str | None = None,
    ssh_user: str | None = None,
    execution_mode: str | None = None,
) -> ExecutionNode:
    """
    Upsert an :class:`ExecutionNode` for an existing EC2 instance.

    - ``node_key`` defaults to ``ec2-{instance_id}``.
    - ``execution_mode`` defaults to ``ssh_docker`` (Docker on the instance reachable via SSH).
    - Sets ``schedulable`` and ``status`` from instance state (running → READY + schedulable).

    Caller should ``commit`` the session. Does not call ``session.commit()``.
    """
    iid = _require_instance_id(instance_id)
    client = ec2_client or build_ec2_client()
    desc = describe_ec2_instance(iid, ec2_client=client)
    key = (node_key or "").strip() or f"ec2-{iid}"
    mode = (execution_mode or ExecutionNodeExecutionMode.SSH_DOCKER.value).strip().lower()
    user = (ssh_user or "").strip() or get_settings().devnest_ec2_ssh_user_default.strip() or "ubuntu"

    vcpu, mem_mb = _instance_type_capacity(client, desc.instance_type)
    running = desc.state == "running"
    status = ExecutionNodeStatus.READY.value if running else ExecutionNodeStatus.NOT_READY.value
    schedulable = running

    stmt = select(ExecutionNode).where(ExecutionNode.provider_instance_id == iid)
    row = session.exec(stmt).first()
    if row is None:
        stmt_key = select(ExecutionNode).where(ExecutionNode.node_key == key)
        row = session.exec(stmt_key).first()

    now = datetime.now(timezone.utc)
    meta_patch = {
        "ec2": {
            "state": desc.state,
            "synced_at": now.isoformat(),
        },
    }

    if row is None:
        row = ExecutionNode(
            node_key=key,
            name=desc.name_tag or key,
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id=iid,
            region=desc.region,
            availability_zone=desc.availability_zone,
            instance_type=desc.instance_type,
            hostname=None,
            private_ip=desc.private_ip,
            public_ip=desc.public_ip,
            execution_mode=mode,
            ssh_host=None,
            ssh_port=22,
            ssh_user=user,
            status=status,
            schedulable=schedulable,
            total_cpu=vcpu,
            total_memory_mb=mem_mb,
            allocatable_cpu=vcpu,
            allocatable_memory_mb=mem_mb,
            metadata_json=meta_patch,
            iam_instance_profile_name=desc.iam_instance_profile_name,
            last_synced_at=now,
        )
        session.add(row)
    else:
        row.node_key = key
        row.name = desc.name_tag or row.name or key
        row.provider_type = ExecutionNodeProviderType.EC2.value
        row.provider_instance_id = iid
        row.region = desc.region
        row.availability_zone = desc.availability_zone
        row.instance_type = desc.instance_type
        row.private_ip = desc.private_ip
        row.public_ip = desc.public_ip
        row.execution_mode = mode
        row.ssh_user = user
        row.status = status
        row.schedulable = schedulable
        row.total_cpu = vcpu
        row.total_memory_mb = mem_mb
        row.allocatable_cpu = vcpu
        row.allocatable_memory_mb = mem_mb
        row.iam_instance_profile_name = desc.iam_instance_profile_name
        row.last_synced_at = now
        merged = dict(row.metadata_json or {})
        inner = dict(merged.get("ec2") or {})
        inner.update(meta_patch["ec2"])
        merged["ec2"] = inner
        row.metadata_json = merged
        row.updated_at = now
        session.add(row)

    session.flush()
    return row


def sync_ec2_instances(
    session: Session,
    *,
    ec2_client: BaseClient | None = None,
    instance_ids: list[str] | None = None,
) -> list[ExecutionNode]:
    """
    Refresh registry rows from EC2.

    - If ``instance_ids`` is set, sync exactly those ids (must exist in AWS).
    - Otherwise sync every DB row with ``provider_type=ec2`` and a non-null ``provider_instance_id``.

    Returns updated :class:`ExecutionNode` instances (caller commits).
    """
    client = ec2_client or build_ec2_client()
    if instance_ids:
        out: list[ExecutionNode] = []
        for raw in instance_ids:
            out.append(register_ec2_instance(session, raw.strip(), ec2_client=client))
        return out

    stmt = select(ExecutionNode).where(ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value)
    rows = [r for r in session.exec(stmt).all() if (r.provider_instance_id or "").strip()]
    updated: list[ExecutionNode] = []
    for row in rows:
        pid = (row.provider_instance_id or "").strip()
        if not pid:
            continue
        updated.append(register_ec2_instance(session, pid, ec2_client=client))
    return updated
