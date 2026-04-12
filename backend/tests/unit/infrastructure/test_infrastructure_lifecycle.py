"""Unit tests: EC2 provisioning (Stubber) and lifecycle transitions (SQLite)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from botocore.stub import ANY, Stubber
from sqlmodel import Session, select

from app.services.infrastructure_service.lifecycle import provision_ec2_node, sync_node_state
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
    get_settings.cache_clear()


def test_sync_promotes_provisioning_to_ready_when_ssm_online(infrastructure_unit_engine) -> None:
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
        metadata_json={},
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
            patch(
                "app.services.infrastructure_service.lifecycle.is_instance_ssm_online",
                return_value=True,
            ),
        ):
            out = sync_node_state(session, node_key="ssm-promote-test")
            session.commit()
            session.refresh(out)

    assert out.status == ExecutionNodeStatus.READY.value
    assert out.schedulable is True
    assert out.private_ip == "10.0.0.10"


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
