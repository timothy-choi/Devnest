"""Unit tests: DevNest-tagged EC2 cleanup (mocked boto3)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.services.infrastructure_service.ec2_cleanup import (
    cleanup_after_instance_terminated,
    cleanup_devnest_autocleanup_orphans,
    devnest_autocleanup_eligible,
    discover_devnest_autocleanup_orphans,
    reconcile_stale_ec2_execution_nodes,
)


@pytest.fixture(autouse=True)
def _bypass_ec2_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.infrastructure_service.ec2_cleanup.client_call_with_throttle_retry",
        lambda _name, fn: fn(),
    )


def _tags(**kw: str) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in kw.items()]


FULL = _tags(
    ManagedBy="DevNest",
    Project="DevNest",
    AutoCleanup="true",
    ExecutionNode="ec2-node-a",
)


class _Paginator:
    def __init__(self, operation_name: str, client: MagicMock) -> None:
        self.operation_name = operation_name
        self._client = client

    def paginate(self, **kwargs):  # noqa: ANN003
        if self.operation_name == "describe_volumes":
            page = self._client.describe_volumes(**kwargs)
            yield {"Volumes": page.get("Volumes", [])}
            return
        if self.operation_name == "describe_network_interfaces":
            page = self._client.describe_network_interfaces(**kwargs)
            yield {"NetworkInterfaces": page.get("NetworkInterfaces", [])}
            return
        if self.operation_name == "describe_security_groups":
            page = self._client.describe_security_groups(**kwargs)
            yield {"SecurityGroups": page.get("SecurityGroups", [])}
            return
        raise AssertionError(self.operation_name)


def test_devnest_autocleanup_eligible_requires_exact_triple() -> None:
    assert devnest_autocleanup_eligible(FULL)
    assert not devnest_autocleanup_eligible(_tags(ManagedBy="DevNest", Project="DevNest", AutoCleanup="true"))
    assert not devnest_autocleanup_eligible(
        _tags(ManagedBy="Other", Project="DevNest", AutoCleanup="true", ExecutionNode="x"),
    )
    assert not devnest_autocleanup_eligible(
        _tags(ManagedBy="DevNest", Project="DevNest", AutoCleanup="false", ExecutionNode="x"),
    )


def test_tagged_available_volume_deleted() -> None:
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _Paginator(op, client)
    vol_id = "vol-aaa"

    def _describe_volumes(**_kw):  # noqa: ANN003
        return {
            "Volumes": [
                {
                    "VolumeId": vol_id,
                    "State": "available",
                    "Attachments": [],
                    "Tags": FULL,
                },
            ],
        }

    client.describe_volumes.side_effect = _describe_volumes
    client.describe_network_interfaces.return_value = {"NetworkInterfaces": []}
    client.describe_security_groups.return_value = {"SecurityGroups": []}
    client.describe_instances.return_value = {"Reservations": []}
    client.describe_addresses.return_value = {"Addresses": []}

    settings = MagicMock()
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    stats = cleanup_devnest_autocleanup_orphans(client, settings)
    assert stats["volumes_deleted"] == 1
    client.delete_volume.assert_called_once_with(VolumeId=vol_id)


def test_untagged_volume_never_deleted() -> None:
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _Paginator(op, client)

    def _describe_volumes(**_kw):  # noqa: ANN003
        return {
            "Volumes": [
                {
                    "VolumeId": "vol-unmanaged",
                    "State": "available",
                    "Attachments": [],
                    "Tags": [{"Key": "Name", "Value": "legacy"}],
                },
            ],
        }

    client.describe_volumes.side_effect = _describe_volumes
    client.describe_network_interfaces.return_value = {"NetworkInterfaces": []}
    client.describe_security_groups.return_value = {"SecurityGroups": []}
    client.describe_addresses.return_value = {"Addresses": []}

    settings = MagicMock()
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    cleanup_devnest_autocleanup_orphans(client, settings)
    client.delete_volume.assert_not_called()


def test_manually_managed_eip_skipped_without_autocleanup_tag() -> None:
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _Paginator(op, client)
    client.describe_volumes.return_value = {"Volumes": []}
    client.describe_network_interfaces.return_value = {"NetworkInterfaces": []}
    client.describe_security_groups.return_value = {"SecurityGroups": []}
    client.describe_instances.return_value = {"Reservations": []}

    client.describe_addresses.return_value = {
        "Addresses": [
            {
                "AllocationId": "eipalloc-manual",
                "PublicIp": "1.2.3.4",
                "Tags": [{"Key": "Name", "Value": "static-nat"}],
            },
        ],
    }

    settings = MagicMock()
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    cleanup_devnest_autocleanup_orphans(client, settings)
    client.release_address.assert_not_called()


def test_tagged_unused_eip_released() -> None:
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _Paginator(op, client)
    client.describe_volumes.return_value = {"Volumes": []}
    client.describe_network_interfaces.return_value = {"NetworkInterfaces": []}
    client.describe_security_groups.return_value = {"SecurityGroups": []}
    client.describe_instances.return_value = {"Reservations": []}

    alloc = "eipalloc-autoclean"
    client.describe_addresses.return_value = {
        "Addresses": [
            {
                "AllocationId": alloc,
                "AssociationId": "",
                "InstanceId": "",
                "NetworkInterfaceId": "",
                "Tags": FULL,
            },
        ],
    }

    settings = MagicMock()
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    stats = cleanup_devnest_autocleanup_orphans(client, settings)
    assert stats["eips_released"] == 1
    client.release_address.assert_called_once_with(AllocationId=alloc)


def test_volume_delete_idempotent_on_second_run() -> None:
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _Paginator(op, client)
    vol_id = "vol-del-twice"

    def _describe_volumes(**_kw):  # noqa: ANN003
        return {
            "Volumes": [
                {
                    "VolumeId": vol_id,
                    "State": "available",
                    "Attachments": [],
                    "Tags": FULL,
                },
            ],
        }

    client.describe_volumes.side_effect = _describe_volumes
    client.describe_network_interfaces.return_value = {"NetworkInterfaces": []}
    client.describe_security_groups.return_value = {"SecurityGroups": []}
    client.describe_instances.return_value = {"Reservations": []}
    client.describe_addresses.return_value = {"Addresses": []}

    client.delete_volume.side_effect = [
        None,
        ClientError({"Error": {"Code": "InvalidVolume.NotFound"}}, "DeleteVolume"),
    ]

    settings = MagicMock()
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    cleanup_devnest_autocleanup_orphans(client, settings)
    cleanup_devnest_autocleanup_orphans(client, settings)
    assert client.delete_volume.call_count == 2


def test_control_plane_instance_skips_post_terminate_cleanup() -> None:
    client = MagicMock()
    settings = MagicMock()
    settings.devnest_aws_control_plane_instance_ids = "i-controlplane0,i-other"
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    cleanup_after_instance_terminated(
        client,
        settings,
        instance_id="i-controlplane0",
        node_key="ec2-node-a",
    )
    client.describe_volumes.assert_not_called()


def test_orphans_report_lists_only_safe_resources() -> None:
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _Paginator(op, client)
    vol_ok = "vol-orphan"

    def _describe_volumes(**_kw):  # noqa: ANN003
        return {
            "Volumes": [
                {
                    "VolumeId": vol_ok,
                    "State": "available",
                    "Attachments": [],
                    "Tags": FULL,
                },
            ],
        }

    client.describe_volumes.side_effect = _describe_volumes
    client.describe_network_interfaces.return_value = {"NetworkInterfaces": []}
    client.describe_security_groups.return_value = {"SecurityGroups": []}
    client.describe_addresses.return_value = {"Addresses": []}

    settings = MagicMock()
    settings.devnest_ec2_orphan_scan_elastic_ips = True

    report = discover_devnest_autocleanup_orphans(client, settings)
    assert len(report.volumes) == 1
    assert report.volumes[0]["volume_id"] == vol_ok


def test_reconcile_marks_terminating_when_instance_missing(
    infrastructure_unit_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlmodel import Session, select

    from app.services.placement_service.models import (
        ExecutionNode,
        ExecutionNodeExecutionMode,
        ExecutionNodeProviderType,
        ExecutionNodeStatus,
    )
    from app.services.providers.errors import Ec2InstanceNotFoundError

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()

    iid = "i-reconcile-deadbeef"
    row = ExecutionNode(
        node_key="rec-node",
        name="rec-node",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=iid,
        region="us-east-1",
        execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ssh_user="ubuntu",
        status=ExecutionNodeStatus.TERMINATING.value,
        schedulable=False,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
        metadata_json={},
    )
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)

    def _missing(*_a, **_k):  # noqa: ANN002
        raise Ec2InstanceNotFoundError()

    monkeypatch.setattr(
        "app.services.infrastructure_service.ec2_cleanup.describe_ec2_instance",
        _missing,
    )

    with Session(infrastructure_unit_engine) as session:
        n = reconcile_stale_ec2_execution_nodes(session)
        session.commit()

    assert n == 1
    with Session(infrastructure_unit_engine) as session:
        r = session.exec(select(ExecutionNode).where(ExecutionNode.id == row.id)).first()
        assert r is not None
        assert r.status == ExecutionNodeStatus.TERMINATED.value

    get_settings.cache_clear()
