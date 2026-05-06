"""Safe AWS cleanup for DevNest-autoscaled EC2 (tag-guarded, idempotent).

Only resources bearing **all** of:

- ``ManagedBy=DevNest``
- ``Project=DevNest``
- ``AutoCleanup=true``
- ``ExecutionNode=<node_key>``

are eligible for automated deletion/release. Untagged or partially tagged resources are never touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

from app.libs.common.config import get_settings
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType, ExecutionNodeStatus
from app.services.providers.aws_throttle import client_call_with_throttle_retry
from app.services.providers.ec2_provider import build_ec2_client, describe_ec2_instance
from app.services.providers.errors import Ec2InstanceNotFoundError

logger = logging.getLogger(__name__)

TAG_MANAGED_BY = "ManagedBy"
TAG_PROJECT = "Project"
TAG_AUTO_CLEANUP = "AutoCleanup"
TAG_EXECUTION_NODE = "ExecutionNode"

VALUE_DEVNEST = "DevNest"
VALUE_AUTO_CLEANUP_TRUE = "true"

_PROTECTED_EXTRA_TAG_KEYS = frozenset(
    {
        "managedby",
        "project",
        "autocleanup",
        "executionnode",
    },
)


def _tag_prefix(settings: Any) -> str:
    return (getattr(settings, "devnest_ec2_tag_prefix", None) or "devnest").strip() or "devnest"


def _tags_list_to_map(tags: list[dict[str, str]] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in tags or []:
        k = str(t.get("Key", "")).strip()
        v = str(t.get("Value", "")).strip()
        if k:
            out[k] = v
    return out


def _normalized_tag_map(tags: list[dict[str, str]] | None) -> dict[str, str]:
    raw = _tags_list_to_map(tags)
    return {k.lower(): v for k, v in raw.items()}


def devnest_autocleanup_eligible(tag_map: dict[str, str] | list[dict[str, str]]) -> bool:
    """True only when required DevNest autocleanup tags are present with exact keys and safe values."""
    raw = _tags_list_to_map(tag_map) if isinstance(tag_map, list) else dict(tag_map)
    if raw.get(TAG_MANAGED_BY) != VALUE_DEVNEST:
        return False
    if raw.get(TAG_PROJECT) != VALUE_DEVNEST:
        return False
    ac = str(raw.get(TAG_AUTO_CLEANUP, "")).strip().lower()
    if ac != VALUE_AUTO_CLEANUP_TRUE:
        return False
    if not (raw.get(TAG_EXECUTION_NODE) or "").strip():
        return False
    return True


def _merge_extra_tags(tags: list[dict[str, str]], extra: dict[str, str] | None) -> None:
    if not extra:
        return
    for tk, tv in extra.items():
        k = str(tk).strip()
        v = str(tv).strip()
        if not k or not v:
            continue
        if k.lower() in _PROTECTED_EXTRA_TAG_KEYS:
            continue
        tags.append({"Key": k[:127], "Value": v[:256]})


def build_launch_tags_for_run_instances(
    settings: Any,
    *,
    node_key: str | None,
    display_name: str,
    extra_tags: dict[str, str] | None,
) -> list[dict[str, str]]:
    """Tags applied at ``run_instances`` for instance + root volume (ExecutionNode omitted until known if absent)."""
    prefix = _tag_prefix(settings)
    name = (display_name or "").strip() or "devnest-node"
    tags: list[dict[str, str]] = [
        {"Key": TAG_MANAGED_BY, "Value": VALUE_DEVNEST},
        {"Key": TAG_PROJECT, "Value": VALUE_DEVNEST},
        {"Key": TAG_AUTO_CLEANUP, "Value": VALUE_AUTO_CLEANUP_TRUE},
        {"Key": "Name", "Value": name[:256]},
        {"Key": f"{prefix}:managed"[:127], "Value": "true"},
    ]
    nk = (node_key or "").strip()
    if nk:
        tags.append({"Key": TAG_EXECUTION_NODE, "Value": nk[:256]})
        tags.append({"Key": f"{prefix}:node_key"[:127], "Value": nk[:256]})
    _merge_extra_tags(tags, extra_tags)
    return tags


def launch_tag_specifications_for_run_instances(
    settings: Any,
    node_key: str | None,
    display_name: str,
    extra_tags: dict[str, str] | None,
) -> list[dict[str, Any]]:
    tags = build_launch_tags_for_run_instances(settings, node_key=node_key, display_name=display_name, extra_tags=extra_tags)
    return [
        {"ResourceType": "instance", "Tags": tags},
        {"ResourceType": "volume", "Tags": tags},
    ]


def build_complete_devnest_autoscale_tags(
    settings: Any,
    node_key: str,
    *,
    display_name: str,
    extra_tags: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Full tag set including ``ExecutionNode`` (post-launch / attach resources)."""
    nk = (node_key or "").strip()
    if not nk:
        raise ValueError("node_key is required for complete DevNest autoscale tags")
    prefix = _tag_prefix(settings)
    name = (display_name or "").strip() or nk
    tags: list[dict[str, str]] = [
        {"Key": TAG_MANAGED_BY, "Value": VALUE_DEVNEST},
        {"Key": TAG_PROJECT, "Value": VALUE_DEVNEST},
        {"Key": TAG_AUTO_CLEANUP, "Value": VALUE_AUTO_CLEANUP_TRUE},
        {"Key": TAG_EXECUTION_NODE, "Value": nk[:256]},
        {"Key": "Name", "Value": name[:256]},
        {"Key": f"{prefix}:managed"[:127], "Value": "true"},
        {"Key": f"{prefix}:node_key"[:127], "Value": nk[:256]},
    ]
    _merge_extra_tags(tags, extra_tags)
    return tags


def resource_ids_from_run_instances_instance(instance_dict: dict[str, Any]) -> list[str]:
    """Collect instance id, boot volume, ENIs, and associated EIP allocation ids when present on the RunInstances response."""
    seen: list[str] = []
    iid = (instance_dict.get("InstanceId") or "").strip()
    if iid:
        seen.append(iid)
    for bd in instance_dict.get("BlockDeviceMappings") or []:
        ebs = bd.get("Ebs") or {}
        vid = (ebs.get("VolumeId") or "").strip()
        if vid:
            seen.append(vid)
    for ni in instance_dict.get("NetworkInterfaces") or []:
        eni = (ni.get("NetworkInterfaceId") or "").strip()
        if eni:
            seen.append(eni)
        assoc = ni.get("Association") or {}
        aid = (assoc.get("AllocationId") or "").strip()
        if aid:
            seen.append(aid)
    return list(dict.fromkeys(seen))


def apply_devnest_tags_to_instance_bundle(
    client: BaseClient,
    settings: Any,
    *,
    node_key: str,
    display_name: str,
    instance_dict: dict[str, Any],
    extra_tags: dict[str, str] | None = None,
) -> None:
    """Idempotent: tag instance, volumes, ENIs, and Elastic IP allocations created with the instance."""
    tags = build_complete_devnest_autoscale_tags(
        settings,
        node_key,
        display_name=display_name,
        extra_tags=extra_tags,
    )
    rids = resource_ids_from_run_instances_instance(instance_dict)
    if not rids:
        return
    try:
        client_call_with_throttle_retry(
            "ec2.CreateTags",
            lambda: client.create_tags(Resources=rids, Tags=tags),
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        logger.warning(
            "ec2_create_tags_failed",
            extra={"resources": rids, "error_code": code, "error": str(e)},
        )


def _control_plane_instance_ids(settings: Any) -> frozenset[str]:
    raw = (getattr(settings, "devnest_aws_control_plane_instance_ids", "") or "").strip()
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _client_error_code(exc: ClientError) -> str:
    return str((exc.response.get("Error") or {}).get("Code", "") or "")


def _volume_attachments(v: dict[str, Any]) -> list[dict[str, Any]]:
    return list(v.get("Attachments") or [])


def _security_group_in_use(client: BaseClient, sg_id: str) -> bool:
    r = client_call_with_throttle_retry(
        "ec2.DescribeNetworkInterfaces",
        lambda: client.describe_network_interfaces(Filters=[{"Name": "group-id", "Values": [sg_id]}]),
    )
    if r.get("NetworkInterfaces"):
        return True
    r2 = client_call_with_throttle_retry(
        "ec2.DescribeInstances",
        lambda: client.describe_instances(
            Filters=[{"Name": "instance.group-id", "Values": [sg_id]}],
        ),
    )
    for res in r2.get("Reservations") or []:
        for inst in res.get("Instances") or []:
            st = str((inst.get("State") or {}).get("Name") or "").lower()
            if st not in ("terminated", "shutting-down"):
                return True
    return False


@dataclass
class AwsOrphansReport:
    volumes: list[dict[str, Any]] = field(default_factory=list)
    network_interfaces: list[dict[str, Any]] = field(default_factory=list)
    elastic_ips: list[dict[str, Any]] = field(default_factory=list)
    security_groups: list[dict[str, Any]] = field(default_factory=list)


def _describe_filtered_volumes(client: BaseClient, *, extra_filters: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    flt: list[dict[str, Any]] = [
        {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [VALUE_DEVNEST]},
        {"Name": f"tag:{TAG_PROJECT}", "Values": [VALUE_DEVNEST]},
        {"Name": f"tag:{TAG_AUTO_CLEANUP}", "Values": [VALUE_AUTO_CLEANUP_TRUE]},
    ]
    if extra_filters:
        flt.extend(extra_filters)
    out: list[dict[str, Any]] = []
    paginator = client.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=flt):
        out.extend(page.get("Volumes") or [])
    return out


def _describe_filtered_enis(client: BaseClient, *, extra_filters: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    flt: list[dict[str, Any]] = [
        {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [VALUE_DEVNEST]},
        {"Name": f"tag:{TAG_PROJECT}", "Values": [VALUE_DEVNEST]},
        {"Name": f"tag:{TAG_AUTO_CLEANUP}", "Values": [VALUE_AUTO_CLEANUP_TRUE]},
    ]
    if extra_filters:
        flt.extend(extra_filters)
    out: list[dict[str, Any]] = []
    paginator = client.get_paginator("describe_network_interfaces")
    for page in paginator.paginate(Filters=flt):
        out.extend(page.get("NetworkInterfaces") or [])
    return out


def _describe_filtered_security_groups(client: BaseClient) -> list[dict[str, Any]]:
    flt: list[dict[str, Any]] = [
        {"Name": f"tag:{TAG_MANAGED_BY}", "Values": [VALUE_DEVNEST]},
        {"Name": f"tag:{TAG_PROJECT}", "Values": [VALUE_DEVNEST]},
        {"Name": f"tag:{TAG_AUTO_CLEANUP}", "Values": [VALUE_AUTO_CLEANUP_TRUE]},
    ]
    out: list[dict[str, Any]] = []
    paginator = client.get_paginator("describe_security_groups")
    for page in paginator.paginate(Filters=flt):
        out.extend(page.get("SecurityGroups") or [])
    return out


def _summarize_volume(v: dict[str, Any]) -> dict[str, Any]:
    tags = _tags_list_to_map(v.get("Tags"))
    return {
        "volume_id": v.get("VolumeId"),
        "state": v.get("State"),
        "tags": tags,
    }


def _summarize_eni(n: dict[str, Any]) -> dict[str, Any]:
    tags = _tags_list_to_map(n.get("TagSet"))
    return {
        "network_interface_id": n.get("NetworkInterfaceId"),
        "status": n.get("Status"),
        "tags": tags,
    }


def _summarize_eip(a: dict[str, Any]) -> dict[str, Any]:
    tags = _tags_list_to_map(a.get("Tags"))
    return {
        "allocation_id": a.get("AllocationId"),
        "public_ip": a.get("PublicIp"),
        "association_id": a.get("AssociationId"),
        "instance_id": a.get("InstanceId"),
        "network_interface_id": a.get("NetworkInterfaceId"),
        "tags": tags,
    }


def _summarize_sg(s: dict[str, Any]) -> dict[str, Any]:
    tags = _tags_list_to_map(s.get("Tags"))
    return {
        "group_id": s.get("GroupId"),
        "group_name": s.get("GroupName"),
        "vpc_id": s.get("VpcId"),
        "tags": tags,
    }


def _describe_all_ec2_addresses(client: BaseClient) -> list[dict[str, Any]]:
    resp = client_call_with_throttle_retry(
        "ec2.DescribeAddresses",
        lambda: client.describe_addresses(),
    )
    return list(resp.get("Addresses") or [])


def discover_devnest_autocleanup_orphans(client: BaseClient, settings: Any) -> AwsOrphansReport:
    """List orphan candidates (tag-complete DevNest autocleanup); does not mutate AWS."""
    report = AwsOrphansReport()
    for v in _describe_filtered_volumes(client):
        if not devnest_autocleanup_eligible(v.get("Tags") or []):
            logger.info(
                "ec2.cleanup.skipped_unmanaged",
                extra={"resource": "volume", "volume_id": v.get("VolumeId"), "reason": "incomplete_tags"},
            )
            continue
        if str(v.get("State") or "").lower() != "available":
            continue
        if _volume_attachments(v):
            continue
        report.volumes.append(_summarize_volume(v))

    for ni in _describe_filtered_enis(client):
        if not devnest_autocleanup_eligible(ni.get("TagSet") or []):
            logger.info(
                "ec2.cleanup.skipped_unmanaged",
                extra={"resource": "network_interface", "eni_id": ni.get("NetworkInterfaceId"), "reason": "incomplete_tags"},
            )
            continue
        if str(ni.get("Status") or "").lower() != "available":
            continue
        report.network_interfaces.append(_summarize_eni(ni))

    if getattr(settings, "devnest_ec2_orphan_scan_elastic_ips", True):
        for a in _describe_all_ec2_addresses(client):
            tags = a.get("Tags") or []
            if not devnest_autocleanup_eligible(tags):
                continue
            if (a.get("AssociationId") or "").strip():
                continue
            if (a.get("InstanceId") or "").strip():
                continue
            if (a.get("NetworkInterfaceId") or "").strip():
                continue
            report.elastic_ips.append(_summarize_eip(a))

    for sg in _describe_filtered_security_groups(client):
        if not devnest_autocleanup_eligible(sg.get("Tags") or []):
            logger.info(
                "ec2.cleanup.skipped_unmanaged",
                extra={"resource": "security_group", "group_id": sg.get("GroupId"), "reason": "incomplete_tags"},
            )
            continue
        gid = (sg.get("GroupId") or "").strip()
        if gid and not _security_group_in_use(client, gid):
            report.security_groups.append(_summarize_sg(sg))

    return report


def cleanup_devnest_autocleanup_orphans(client: BaseClient, settings: Any) -> dict[str, int]:
    """Delete/release safe orphans; idempotent."""
    report = discover_devnest_autocleanup_orphans(client, settings)
    deleted_v = deleted_e = released_eip = deleted_sg = 0
    for v in report.volumes:
        vid = str(v.get("volume_id") or "").strip()
        if not vid:
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.DeleteVolume",
                lambda: client.delete_volume(VolumeId=vid),
            )
            deleted_v += 1
            logger.info("ec2.cleanup.volume_deleted", extra={"volume_id": vid})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidVolume.NotFound",):
                continue
            logger.warning("ec2.cleanup.failed", extra={"resource": "volume", "volume_id": vid, "error_code": code, "error": str(e)})

    for n in report.network_interfaces:
        eid = str(n.get("network_interface_id") or "").strip()
        if not eid:
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.DeleteNetworkInterface",
                lambda nid=eid: client.delete_network_interface(NetworkInterfaceId=nid),
            )
            deleted_e += 1
            logger.info("ec2.cleanup.eni_deleted", extra={"network_interface_id": eid})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidNetworkInterfaceID.NotFound",):
                continue
            logger.warning(
                "ec2.cleanup.failed",
                extra={"resource": "network_interface", "network_interface_id": eid, "error_code": code, "error": str(e)},
            )

    for a in report.elastic_ips:
        alloc = str(a.get("allocation_id") or "").strip()
        if not alloc:
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.ReleaseAddress",
                lambda aid=alloc: client.release_address(AllocationId=aid),
            )
            released_eip += 1
            logger.info("ec2.cleanup.eip_released", extra={"allocation_id": alloc})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidAllocationID.NotFound",):
                continue
            logger.warning(
                "ec2.cleanup.failed",
                extra={"resource": "elastic_ip", "allocation_id": alloc, "error_code": code, "error": str(e)},
            )

    for s in report.security_groups:
        gid = str(s.get("group_id") or "").strip()
        if not gid:
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.DeleteSecurityGroup",
                lambda sg_id=gid: client.delete_security_group(GroupId=sg_id),
            )
            deleted_sg += 1
            logger.info("ec2.cleanup.security_group_deleted", extra={"group_id": gid})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidGroup.NotFound",):
                continue
            logger.warning(
                "ec2.cleanup.failed",
                extra={"resource": "security_group", "group_id": gid, "error_code": code, "error": str(e)},
            )

    return {
        "volumes_deleted": deleted_v,
        "enis_deleted": deleted_e,
        "eips_released": released_eip,
        "security_groups_deleted": deleted_sg,
    }


def cleanup_after_instance_terminated(
    client: BaseClient,
    settings: Any,
    *,
    instance_id: str,
    node_key: str,
) -> None:
    """Post-terminate sweep for resources tagged for this execution node."""
    iid = (instance_id or "").strip()
    nk = (node_key or "").strip()
    if not iid or not nk:
        return
    if iid in _control_plane_instance_ids(settings):
        logger.info(
            "ec2.cleanup.skipped_unmanaged",
            extra={"reason": "control_plane_instance", "instance_id": iid},
        )
        return

    logger.info(
        "ec2.cleanup.started",
        extra={"instance_id": iid, "node_key": nk, "phase": "post_terminate"},
    )
    logger.info("ec2.cleanup.instance_terminated", extra={"instance_id": iid, "node_key": nk})

    extra_vol_filters = [{"Name": f"tag:{TAG_EXECUTION_NODE}", "Values": [nk]}]
    for v in _describe_filtered_volumes(client, extra_filters=extra_vol_filters):
        if not devnest_autocleanup_eligible(v.get("Tags") or []):
            logger.info(
                "ec2.cleanup.skipped_unmanaged",
                extra={"resource": "volume", "volume_id": v.get("VolumeId"), "reason": "incomplete_tags"},
            )
            continue
        if str(v.get("State") or "").lower() != "available":
            continue
        if _volume_attachments(v):
            continue
        vid = str(v.get("VolumeId") or "").strip()
        if not vid:
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.DeleteVolume",
                lambda: client.delete_volume(VolumeId=vid),
            )
            logger.info("ec2.cleanup.volume_deleted", extra={"volume_id": vid, "node_key": nk})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidVolume.NotFound",):
                continue
            logger.warning(
                "ec2.cleanup.failed",
                extra={"resource": "volume", "volume_id": vid, "error_code": code, "error": str(e)},
            )

    extra_eni_filters = [{"Name": f"tag:{TAG_EXECUTION_NODE}", "Values": [nk]}]
    for ni in _describe_filtered_enis(client, extra_filters=extra_eni_filters):
        if not devnest_autocleanup_eligible(ni.get("TagSet") or []):
            logger.info(
                "ec2.cleanup.skipped_unmanaged",
                extra={"resource": "network_interface", "eni_id": ni.get("NetworkInterfaceId"), "reason": "incomplete_tags"},
            )
            continue
        if str(ni.get("Status") or "").lower() != "available":
            continue
        eid = str(ni.get("NetworkInterfaceId") or "").strip()
        if not eid:
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.DeleteNetworkInterface",
                lambda: client.delete_network_interface(NetworkInterfaceId=eid),
            )
            logger.info("ec2.cleanup.eni_deleted", extra={"network_interface_id": eid, "node_key": nk})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidNetworkInterfaceID.NotFound",):
                continue
            logger.warning(
                "ec2.cleanup.failed",
                extra={"resource": "network_interface", "network_interface_id": eid, "error_code": code, "error": str(e)},
            )

    if getattr(settings, "devnest_ec2_orphan_scan_elastic_ips", True):
        for a in _describe_all_ec2_addresses(client):
            tags = a.get("Tags") or []
            if not devnest_autocleanup_eligible(tags):
                continue
            tmap = _normalized_tag_map(tags)
            if (tmap.get("executionnode") or "").strip() != nk:
                continue
            if (a.get("AssociationId") or "").strip():
                logger.info(
                    "ec2.cleanup.skipped_unmanaged",
                    extra={"resource": "elastic_ip", "allocation_id": a.get("AllocationId"), "reason": "still_associated"},
                )
                continue
            alloc = str(a.get("AllocationId") or "").strip()
            if not alloc:
                continue
            try:
                client_call_with_throttle_retry(
                    "ec2.ReleaseAddress",
                    lambda: client.release_address(AllocationId=alloc),
                )
                logger.info("ec2.cleanup.eip_released", extra={"allocation_id": alloc, "node_key": nk})
            except ClientError as e:
                code = _client_error_code(e)
                if code in ("InvalidAllocationID.NotFound",):
                    continue
                logger.warning(
                    "ec2.cleanup.failed",
                    extra={"resource": "elastic_ip", "allocation_id": alloc, "error_code": code, "error": str(e)},
                )

    for sg in _describe_filtered_security_groups(client):
        if not devnest_autocleanup_eligible(sg.get("Tags") or []):
            continue
        tmap = _normalized_tag_map(sg.get("Tags") or [])
        if (tmap.get("executionnode") or "").strip() != nk:
            continue
        gid = str(sg.get("GroupId") or "").strip()
        if not gid or _security_group_in_use(client, gid):
            continue
        try:
            client_call_with_throttle_retry(
                "ec2.DeleteSecurityGroup",
                lambda: client.delete_security_group(GroupId=gid),
            )
            logger.info("ec2.cleanup.security_group_deleted", extra={"group_id": gid, "node_key": nk})
        except ClientError as e:
            code = _client_error_code(e)
            if code in ("InvalidGroup.NotFound",):
                continue
            logger.warning(
                "ec2.cleanup.failed",
                extra={"resource": "security_group", "group_id": gid, "error_code": code, "error": str(e)},
            )


def reconcile_stale_ec2_execution_nodes(session: Session) -> int:
    """Mark TERMINATED when AWS instance is gone or fully terminated (never touches control-plane ids)."""
    settings = get_settings()
    region = (settings.aws_region or "").strip()
    if not region:
        return 0
    control = _control_plane_instance_ids(settings)
    client = build_ec2_client(region=region)
    stmt = select(ExecutionNode).where(
        and_(
            ExecutionNode.provider_type == ExecutionNodeProviderType.EC2.value,
            col(ExecutionNode.provider_instance_id).isnot(None),
            ExecutionNode.provider_instance_id != "",
            or_(
                ExecutionNode.status == ExecutionNodeStatus.TERMINATING.value,
                ExecutionNode.status == ExecutionNodeStatus.ERROR.value,
            ),
        ),
    )
    updated = 0
    now = datetime.now(timezone.utc)
    for row in session.exec(stmt).all():
        iid = (row.provider_instance_id or "").strip()
        if not iid or iid.startswith("catalog-pending:"):
            continue
        if iid in control:
            continue
        try:
            desc = describe_ec2_instance(iid, ec2_client=client)
            st = str(desc.state or "").lower()
        except Ec2InstanceNotFoundError:
            st = "terminated"
        if st in ("terminated", "shutting-down"):
            if row.status != ExecutionNodeStatus.TERMINATED.value:
                row.status = ExecutionNodeStatus.TERMINATED.value
                row.schedulable = False
                row.updated_at = now
                session.add(row)
                updated += 1
    return updated
