"""Unit tests: EC2 provisioning (Stubber) and lifecycle transitions (SQLite)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.stub import ANY, Stubber
from sqlmodel import Session, select

from app.services.infrastructure_service.errors import NodeLifecycleError
from app.services.infrastructure_service.lifecycle import (
    mark_node_draining,
    provision_ec2_node,
    register_catalog_ec2_stub,
    sync_node_state,
    terminate_ec2_node,
    undrain_node,
)
from app.services.infrastructure_service.models import Ec2ProvisionRequest
from app.services.placement_service.errors import NoSchedulableNodeError
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)
from app.services.placement_service.node_placement import select_node_for_workspace


def test_provision_ec2_node_creates_provisioning_row(infrastructure_unit_engine, monkeypatch) -> None:
    monkeypatch.setenv("DEVNEST_EC2_TAG_PREFIX", "devnest")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    iid = "i-0a1b2c3d4e5f6789"
    region = "us-east-1"
    client = boto3.client("ec2", region_name=region)
    stubber = Stubber(client)
    stubber.add_response(
        "run_instances",
        {"Instances": [{"InstanceId": iid}]},
        {
            "ImageId": "ami-12345678",
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": "t3.micro",
            "SubnetId": "subnet-aaaabbbb",
            "SecurityGroupIds": ["sg-test123"],
            "TagSpecifications": ANY,
            "UserData": "#!/bin/bash\necho devnest\n",
        },
    )
    stubber.add_response(
        "create_tags",
        {},
        {"Resources": [iid], "Tags": ANY},
    )
    stubber.add_response(
        "describe_instance_types",
        {
            "InstanceTypes": [
                {
                    "InstanceType": "t3.micro",
                    "VCpuInfo": {"DefaultVCpus": 2},
                    "MemoryInfo": {"SizeInMiB": 1024},
                },
            ],
        },
        {"InstanceTypes": ["t3.micro"]},
    )
    stubber.activate()
    req = Ec2ProvisionRequest(
        ami_id="ami-12345678",
        instance_type="t3.micro",
        subnet_id="subnet-aaaabbbb",
        security_group_ids=["sg-test123"],
        iam_instance_profile_name=None,
        region=region,
        node_key="stub-node-a",
        user_data="#!/bin/bash\necho devnest\n",
    )
    try:
        with Session(infrastructure_unit_engine) as session:
            node = provision_ec2_node(session, req, ec2_client=client, wait_until_running=False)
            session.commit()
            session.refresh(node)
    finally:
        stubber.deactivate()

    assert node.node_key == "stub-node-a"
    assert node.provider_type == ExecutionNodeProviderType.EC2.value
    assert node.status == ExecutionNodeStatus.PROVISIONING.value
    assert node.schedulable is False
    assert node.provider_instance_id == iid
    assert node.default_topology_id == 1
    get_settings.cache_clear()


def test_provision_ec2_node_persists_private_ip_from_run_instances(infrastructure_unit_engine, monkeypatch) -> None:
    monkeypatch.setenv("DEVNEST_EC2_TAG_PREFIX", "devnest")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    iid = "i-0a1b2c3d4e5f6790"
    region = "us-east-1"
    client = boto3.client("ec2", region_name=region)
    stubber = Stubber(client)
    stubber.add_response(
        "run_instances",
        {
            "Instances": [
                {
                    "InstanceId": iid,
                    "PrivateIpAddress": "172.30.4.25",
                    "PublicIpAddress": "54.1.2.3",
                    "Placement": {"AvailabilityZone": "us-east-1a"},
                }
            ]
        },
        {
            "ImageId": "ami-12345678",
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": "t3.micro",
            "SubnetId": "subnet-aaaabbbb",
            "SecurityGroupIds": ["sg-test123"],
            "TagSpecifications": ANY,
        },
    )
    stubber.add_response("create_tags", {}, {"Resources": [iid], "Tags": ANY})
    stubber.add_response(
        "describe_instance_types",
        {
            "InstanceTypes": [
                {
                    "InstanceType": "t3.micro",
                    "VCpuInfo": {"DefaultVCpus": 2},
                    "MemoryInfo": {"SizeInMiB": 1024},
                },
            ],
        },
        {"InstanceTypes": ["t3.micro"]},
    )
    stubber.activate()
    req = Ec2ProvisionRequest(
        ami_id="ami-12345678",
        instance_type="t3.micro",
        subnet_id="subnet-aaaabbbb",
        security_group_ids=["sg-test123"],
        region=region,
        node_key="private-ip-node",
    )
    try:
        with Session(infrastructure_unit_engine) as session:
            node = provision_ec2_node(session, req, ec2_client=client, wait_until_running=False)
            session.commit()
            session.refresh(node)
    finally:
        stubber.deactivate()
        get_settings.cache_clear()

    assert node.private_ip == "172.30.4.25"
    assert node.public_ip == "54.1.2.3"
    assert node.availability_zone == "us-east-1a"


def test_provision_run_instances_throttle_retry_then_success(
    infrastructure_unit_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.providers.aws_throttle.time.sleep",
        lambda *_a, **_k: None,
    )
    iid = "i-0a1b2c3d4e5f6789"
    region = "us-east-1"
    client = boto3.client("ec2", region_name=region)
    stubber = Stubber(client)
    common = {
        "ImageId": "ami-12345678",
        "MinCount": 1,
        "MaxCount": 1,
        "InstanceType": "t3.micro",
        "SubnetId": "subnet-aaaabbbb",
        "SecurityGroupIds": ["sg-test123"],
        "TagSpecifications": ANY,
    }
    stubber.add_client_error(
        "run_instances",
        "ThrottlingException",
        expected_params=common,
    )
    stubber.add_response("run_instances", {"Instances": [{"InstanceId": iid}]}, common)
    stubber.add_response("create_tags", {}, {"Resources": [iid], "Tags": ANY})
    stubber.add_response(
        "describe_instance_types",
        {
            "InstanceTypes": [
                {
                    "InstanceType": "t3.micro",
                    "VCpuInfo": {"DefaultVCpus": 2},
                    "MemoryInfo": {"SizeInMiB": 1024},
                },
            ],
        },
        {"InstanceTypes": ["t3.micro"]},
    )
    stubber.activate()
    req = Ec2ProvisionRequest(
        ami_id="ami-12345678",
        instance_type="t3.micro",
        subnet_id="subnet-aaaabbbb",
        security_group_ids=["sg-test123"],
        region=region,
        node_key="throttle-node",
    )
    try:
        with Session(infrastructure_unit_engine) as session:
            node = provision_ec2_node(session, req, ec2_client=client, wait_until_running=False)
            session.commit()
            assert node.provider_instance_id == iid
    finally:
        stubber.deactivate()


def test_provision_ec2_node_sends_user_data_to_run_instances(infrastructure_unit_engine) -> None:
    iid = "i-0a1b2c3d4e5f6789"
    region = "us-east-1"
    user_data = "#!/bin/bash\necho bootstrap\n"
    client = boto3.client("ec2", region_name=region)
    stubber = Stubber(client)
    stubber.add_response(
        "run_instances",
        {"Instances": [{"InstanceId": iid}]},
        {
            "ImageId": "ami-12345678",
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": "t3.micro",
            "SubnetId": "subnet-aaaabbbb",
            "SecurityGroupIds": ["sg-test123"],
            "TagSpecifications": ANY,
            "UserData": user_data,
        },
    )
    stubber.add_response("create_tags", {}, {"Resources": [iid], "Tags": ANY})
    stubber.add_response(
        "describe_instance_types",
        {
            "InstanceTypes": [
                {
                    "InstanceType": "t3.micro",
                    "VCpuInfo": {"DefaultVCpus": 2},
                    "MemoryInfo": {"SizeInMiB": 1024},
                },
            ],
        },
        {"InstanceTypes": ["t3.micro"]},
    )
    stubber.activate()
    req = Ec2ProvisionRequest(
        ami_id="ami-12345678",
        instance_type="t3.micro",
        subnet_id="subnet-aaaabbbb",
        security_group_ids=["sg-test123"],
        region=region,
        node_key="user-data-node",
        user_data=user_data,
    )
    try:
        with Session(infrastructure_unit_engine) as session:
            provision_ec2_node(session, req, ec2_client=client, wait_until_running=False)
    finally:
        stubber.deactivate()


def test_mark_node_draining_idempotent(infrastructure_unit_engine) -> None:
    row = ExecutionNode(
        node_key="drain-twice",
        name="drain-twice",
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
        status=ExecutionNodeStatus.READY.value,
        schedulable=True,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
        metadata_json={},
    )
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()
        first = mark_node_draining(session, node_key="drain-twice")
        session.flush()
        meta1 = dict(first.metadata_json or {})
        st1 = first.status
        second = mark_node_draining(session, node_key="drain-twice")
        meta2 = dict(second.metadata_json or {})
        st2 = second.status
        session.commit()
    assert st1 == ExecutionNodeStatus.DRAINING.value
    assert st2 == ExecutionNodeStatus.DRAINING.value
    assert meta1.get("lifecycle", {}).get("draining_marked_at") == meta2.get("lifecycle", {}).get(
        "draining_marked_at",
    )


def test_undrain_after_drain_restores_ready(infrastructure_unit_engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        row = ExecutionNode(
            node_key="undrain-me",
            name="undrain-me",
            provider_type=ExecutionNodeProviderType.LOCAL.value,
            execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=2.0,
            total_memory_mb=4096,
            allocatable_cpu=2.0,
            allocatable_memory_mb=4096,
            metadata_json={},
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        mark_node_draining(session, node_key="undrain-me")
        session.commit()
        out = undrain_node(session, node_key="undrain-me")
        session.commit()
        session.refresh(out)
    assert out.status == ExecutionNodeStatus.READY.value
    assert out.schedulable is True


def test_register_catalog_ec2_stub_inserts_node2_like_row(infrastructure_unit_engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        node = register_catalog_ec2_stub(
            session,
            node_key="node-2",
            name="staging node 2 catalog",
            provider_instance_id="i-placeholder00000000",
            private_ip="10.0.2.10",
            public_ip="203.0.113.50",
            region="us-east-1",
            execution_mode="ssm_docker",
            status="NOT_READY",
        )
        session.commit()
        session.refresh(node)
        assert node.node_key == "node-2"
        assert node.provider_type == ExecutionNodeProviderType.EC2.value
        assert node.schedulable is False
        assert node.status == ExecutionNodeStatus.NOT_READY.value
        assert node.provider_instance_id == "i-placeholder00000000"
        assert node.private_ip == "10.0.2.10"
        assert node.public_ip == "203.0.113.50"
        assert node.region == "us-east-1"
        assert node.execution_mode == ExecutionNodeExecutionMode.SSM_DOCKER.value


def test_undrain_terminated_raises(infrastructure_unit_engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        row = ExecutionNode(
            node_key="no-undrain-term",
            name="no-undrain-term",
            provider_type=ExecutionNodeProviderType.LOCAL.value,
            execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
            status=ExecutionNodeStatus.TERMINATED.value,
            schedulable=False,
            total_cpu=2.0,
            total_memory_mb=4096,
            allocatable_cpu=2.0,
            allocatable_memory_mb=4096,
            metadata_json={},
        )
        session.add(row)
        session.commit()
        with pytest.raises(NodeLifecycleError, match="cannot undrain TERMINATED"):
            undrain_node(session, node_key="no-undrain-term")


def test_sync_promotes_provisioning_to_ready_when_heartbeat_ready(infrastructure_unit_engine) -> None:
    iid = "i-0123456789abcdef0"
    row = ExecutionNode(
        node_key="ssm-promote-test",
        name="ssm-promote-test",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=iid,
        region="us-east-1",
        execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ssh_user="ubuntu",
        status=ExecutionNodeStatus.PROVISIONING.value,
        schedulable=False,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
        last_heartbeat_at=datetime.now(timezone.utc),
        metadata_json={"heartbeat": {"docker_ok": True, "disk_free_mb": 99_999}},
    )
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)

        def _fake_register(sess, instance_id, *, ec2_client=None, node_key=None, ssh_user=None, execution_mode=None):
            r = sess.exec(select(ExecutionNode).where(ExecutionNode.node_key == "ssm-promote-test")).first()
            assert r is not None
            r.private_ip = "10.0.0.10"
            sess.add(r)
            sess.flush()

        with (
            patch(
                "app.services.infrastructure_service.lifecycle.register_ec2_instance",
                side_effect=_fake_register,
            ),
            patch(
                "app.services.infrastructure_service.lifecycle.describe_ec2_instance",
                return_value=SimpleNamespace(state="running"),
            ),
        ):
            out = sync_node_state(session, node_key="ssm-promote-test")
            session.commit()
            session.refresh(out)

    assert out.status == ExecutionNodeStatus.READY.value
    assert out.schedulable is True
    assert out.private_ip == "10.0.0.10"


def test_sync_promotes_not_ready_to_ready_when_heartbeat_ready(infrastructure_unit_engine) -> None:
    iid = "i-0123456789abcdef1"
    row = ExecutionNode(
        node_key="not-ready-promote-test",
        name="not-ready-promote-test",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=iid,
        region="us-east-1",
        execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ssh_user="ubuntu",
        status=ExecutionNodeStatus.NOT_READY.value,
        schedulable=False,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
        last_heartbeat_at=datetime.now(timezone.utc),
        metadata_json={"heartbeat": {"docker_ok": True, "disk_free_mb": 99_999}},
    )
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()

        def _fake_register(sess, instance_id, *, ec2_client=None, node_key=None, ssh_user=None, execution_mode=None):
            r = sess.exec(select(ExecutionNode).where(ExecutionNode.node_key == "not-ready-promote-test")).first()
            assert r is not None
            r.status = ExecutionNodeStatus.READY.value
            r.schedulable = True
            sess.add(r)
            sess.flush()

        with (
            patch(
                "app.services.infrastructure_service.lifecycle.register_ec2_instance",
                side_effect=_fake_register,
            ),
            patch(
                "app.services.infrastructure_service.lifecycle.describe_ec2_instance",
                return_value=SimpleNamespace(state="running"),
            ),
        ):
            out = sync_node_state(session, node_key="not-ready-promote-test")
            session.commit()
            session.refresh(out)

    assert out.status == ExecutionNodeStatus.READY.value
    assert out.schedulable is True


def test_sync_does_not_promote_provisioning_without_healthy_heartbeat(infrastructure_unit_engine) -> None:
    iid = "i-0123456789abcdeff"
    row = ExecutionNode(
        node_key="ssm-waits-heartbeat",
        name="ssm-waits-heartbeat",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=iid,
        region="us-east-1",
        execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ssh_user="ubuntu",
        status=ExecutionNodeStatus.PROVISIONING.value,
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

        def _fake_register(sess, instance_id, *, ec2_client=None, node_key=None, ssh_user=None, execution_mode=None):
            r = sess.exec(select(ExecutionNode).where(ExecutionNode.node_key == "ssm-waits-heartbeat")).first()
            assert r is not None
            sess.add(r)
            sess.flush()

        with (
            patch(
                "app.services.infrastructure_service.lifecycle.register_ec2_instance",
                side_effect=_fake_register,
            ),
            patch(
                "app.services.infrastructure_service.lifecycle.describe_ec2_instance",
                return_value=SimpleNamespace(state="running"),
            ),
        ):
            out = sync_node_state(session, node_key="ssm-waits-heartbeat")
            session.commit()
            session.refresh(out)

    assert out.status == ExecutionNodeStatus.PROVISIONING.value
    assert out.schedulable is False


def test_mark_node_draining_skips_terminated(infrastructure_unit_engine) -> None:
    row = ExecutionNode(
        node_key="drain-term",
        name="drain-term",
        provider_type=ExecutionNodeProviderType.LOCAL.value,
        execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
        status=ExecutionNodeStatus.TERMINATED.value,
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
        out = mark_node_draining(session, node_key="drain-term")
        st = out.status
        session.commit()
    assert st == ExecutionNodeStatus.TERMINATED.value


def test_terminate_ec2_retry_when_already_terminating_calls_aws_once(
    infrastructure_unit_engine,
) -> None:
    iid = "i-0a1b2c3d4e5f6789"
    row = ExecutionNode(
        node_key="term-retry",
        name="term-retry",
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
        metadata_json={"lifecycle": {"terminate_requested_at": "2020-01-01T00:00:00+00:00"}},
    )
    mock_ec2 = MagicMock()
    mock_ec2.terminate_instances.return_value = {}
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        with patch(
            "app.services.infrastructure_service.lifecycle.describe_ec2_instance",
            return_value=SimpleNamespace(state="terminated"),
        ):
            terminate_ec2_node(
                session,
                node_key="term-retry",
                ec2_client=mock_ec2,
                wait_until_terminated=False,
            )
        session.commit()
    mock_ec2.terminate_instances.assert_called_once()


def test_terminate_ec2_already_terminated_skips_aws_call(infrastructure_unit_engine) -> None:
    iid = "i-0a1b2c3d4e5f6789"
    row = ExecutionNode(
        node_key="term-noop",
        name="term-noop",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id=iid,
        region="us-east-1",
        execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ssh_user="ubuntu",
        status=ExecutionNodeStatus.TERMINATED.value,
        schedulable=False,
        total_cpu=2.0,
        total_memory_mb=4096,
        allocatable_cpu=2.0,
        allocatable_memory_mb=4096,
        metadata_json={},
    )
    mock_ec2 = MagicMock()
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        out = terminate_ec2_node(
            session,
            node_key="term-noop",
            ec2_client=mock_ec2,
            wait_until_terminated=False,
        )
        final_status = out.status
        session.commit()
    mock_ec2.terminate_instances.assert_not_called()
    assert final_status == ExecutionNodeStatus.TERMINATED.value


def test_placement_ec2_pool_skips_provisioning_node(infrastructure_unit_engine, monkeypatch) -> None:
    monkeypatch.setenv("DEVNEST_NODE_PROVIDER", "ec2")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    row = ExecutionNode(
        node_key="ec2-prov-only",
        name="ec2-prov-only",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id="i-0a0a0a0a0a0a0a0a",
        region="us-east-1",
        execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
        ssh_user="ubuntu",
        status=ExecutionNodeStatus.PROVISIONING.value,
        schedulable=False,
        total_cpu=8.0,
        total_memory_mb=16384,
        allocatable_cpu=8.0,
        allocatable_memory_mb=16384,
        metadata_json={},
    )
    with Session(infrastructure_unit_engine) as session:
        session.add(row)
        session.commit()
        with pytest.raises(NoSchedulableNodeError):
            select_node_for_workspace(session, workspace_id=1)
    get_settings.cache_clear()
