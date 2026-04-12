"""Integration: EC2 registry with moto + PostgreSQL schema (same stack as app)."""

from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.placement_service.models import ExecutionNode
from app.services.providers.ec2_provider import list_ec2_instances, register_ec2_instance

pytestmark = pytest.mark.integration


@mock_aws
def test_register_ec2_instance_persists_execution_node(db_session: Session) -> None:
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    get_settings.cache_clear()

    ec2 = boto3.client("ec2", region_name="us-east-1")
    r = ec2.run_instances(
        ImageId="ami-12345678",
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.micro",
    )
    iid = r["Instances"][0]["InstanceId"]

    node = register_ec2_instance(db_session, iid, ec2_client=ec2)
    db_session.commit()

    row = db_session.exec(select(ExecutionNode).where(ExecutionNode.node_key == node.node_key)).first()
    assert row is not None
    assert row.provider_instance_id == iid
    assert row.provider_type == "ec2"


@mock_aws
def test_list_ec2_instances_moto_sees_running_instance(db_session: Session) -> None:
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    get_settings.cache_clear()

    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.run_instances(
        ImageId="ami-12345678",
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.micro",
    )
    listed = list_ec2_instances(
        ec2_client=ec2,
        filters=[
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ],
    )
    assert len(listed) >= 1
