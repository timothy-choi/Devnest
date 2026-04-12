"""Unit tests: EC2 provider with botocore Stubber (no live AWS)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import boto3
from botocore.stub import Stubber
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.services.providers.ec2_provider import (
    describe_ec2_instance,
    list_ec2_instances,
    register_ec2_instance,
)
from app.services.providers.errors import Ec2InstanceNotFoundError, Ec2ProviderError
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _stub_ec2_client(region: str = "us-east-1"):
    return boto3.client("ec2", region_name=region)


def test_describe_ec2_instance_parses_response() -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0a1b2c3d4e5f6789",
                            "State": {"Name": "running"},
                            "InstanceType": "t3.micro",
                            "PrivateIpAddress": "10.0.0.5",
                            "PublicIpAddress": "1.2.3.4",
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                            "Tags": [{"Key": "Name", "Value": "worker-1"}],
                            "IamInstanceProfile": {
                                "Arn": "arn:aws:iam::123456789012:instance-profile/my-profile",
                                "Id": "AIPA",
                            },
                        },
                    ],
                },
            ],
        },
        {"InstanceIds": ["i-0a1b2c3d4e5f6789"]},
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
    d = describe_ec2_instance("i-0a1b2c3d4e5f6789", ec2_client=client)
    assert d.state == "running"
    assert d.private_ip == "10.0.0.5"
    assert d.public_ip == "1.2.3.4"
    assert d.availability_zone == "us-east-1a"
    assert d.instance_type == "t3.micro"
    assert d.name_tag == "worker-1"
    assert d.iam_instance_profile_name == "my-profile"
    stubber.deactivate()


def test_describe_ec2_instance_not_found() -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    stubber.add_client_error(
        "describe_instances",
        service_error_code="InvalidInstanceID.NotFound",
        service_message="not found",
        http_status_code=400,
        expected_params={"InstanceIds": ["i-09999999999999999"]},
    )
    stubber.activate()
    with pytest.raises(Ec2InstanceNotFoundError):
        describe_ec2_instance("i-09999999999999999", ec2_client=client)
    stubber.deactivate()


def test_describe_instances_unauthorized_maps_to_provider_error() -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    stubber.add_client_error(
        "describe_instances",
        service_error_code="UnauthorizedOperation",
        service_message="not allowed",
        http_status_code=403,
        expected_params={"InstanceIds": ["i-0a1b2c3d4e5f6788"]},
    )
    stubber.activate()
    with pytest.raises(Ec2ProviderError, match="AWS denied EC2 describe_instances"):
        describe_ec2_instance("i-0a1b2c3d4e5f6788", ec2_client=client)
    stubber.deactivate()


def test_invalid_instance_id_raises() -> None:
    client = _stub_ec2_client()
    with pytest.raises(Ec2ProviderError, match="invalid EC2 instance id"):
        describe_ec2_instance("not-an-id", ec2_client=client)


def test_list_ec2_instances_empty() -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    stubber.add_response(
        "describe_instances",
        {"Reservations": []},
        {"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]},
    )
    stubber.activate()
    assert list_ec2_instances(ec2_client=client) == []
    stubber.deactivate()


def test_register_rejects_node_key_used_by_local_node(sqlite_engine) -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    iid = "i-0a1b2c3d4e5f6799"
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": iid,
                            "State": {"Name": "running"},
                            "InstanceType": "t3.micro",
                            "PrivateIpAddress": "10.0.0.1",
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                            "Tags": [],
                        },
                    ],
                },
            ],
        },
        {"InstanceIds": [iid]},
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
    with Session(sqlite_engine) as session:
        session.add(
            ExecutionNode(
                node_key="reserved-key",
                name="local",
                provider_type=ExecutionNodeProviderType.LOCAL.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        with pytest.raises(Ec2ProviderError, match="already used by a local execution node"):
            register_ec2_instance(session, iid, ec2_client=client, node_key="reserved-key")
    stubber.deactivate()


def test_register_rejects_node_key_bound_to_other_instance(sqlite_engine) -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    iid_new = "i-0a1b2c3d4e5f6703"
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": iid_new,
                            "State": {"Name": "running"},
                            "InstanceType": "t3.micro",
                            "PrivateIpAddress": "10.0.0.2",
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                            "Tags": [],
                        },
                    ],
                },
            ],
        },
        {"InstanceIds": [iid_new]},
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
    with Session(sqlite_engine) as session:
        session.add(
            ExecutionNode(
                node_key="shared-key",
                name="old",
                provider_type=ExecutionNodeProviderType.EC2.value,
                provider_instance_id="i-0aaaaaaaaaaaaaaaa",
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
                private_ip="10.0.0.99",
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        with pytest.raises(Ec2ProviderError, match="already bound to instance"):
            register_ec2_instance(session, iid_new, ec2_client=client, node_key="shared-key")
    stubber.deactivate()


def test_register_ec2_instance_inserts_row(sqlite_engine) -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    iid = "i-0a1b2c3d4e5f6701"
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": iid,
                            "State": {"Name": "running"},
                            "InstanceType": "t3.micro",
                            "PrivateIpAddress": "10.0.0.8",
                            "Placement": {"AvailabilityZone": "us-east-1b"},
                            "Tags": [],
                        },
                    ],
                },
            ],
        },
        {"InstanceIds": [iid]},
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
    sm = MagicMock()
    sm.devnest_ec2_default_execution_mode = "ssm_docker"
    sm.devnest_ec2_ssh_user_default = "ubuntu"
    with patch("app.services.providers.ec2_provider.get_settings", return_value=sm):
        with Session(sqlite_engine) as session:
            node = register_ec2_instance(session, iid, ec2_client=client)
            session.commit()
            assert node.node_key == f"ec2-{iid}"
            assert node.provider_type == ExecutionNodeProviderType.EC2.value
            assert node.private_ip == "10.0.0.8"
            assert node.last_synced_at is not None
            assert node.schedulable is True
            assert node.execution_mode == ExecutionNodeExecutionMode.SSM_DOCKER.value
    stubber.deactivate()


def test_register_ec2_instance_stopped_not_schedulable(sqlite_engine) -> None:
    client = _stub_ec2_client()
    stubber = Stubber(client)
    iid = "i-0a1b2c3d4e5f6702"
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": iid,
                            "State": {"Name": "stopped"},
                            "InstanceType": "t3.micro",
                            "PrivateIpAddress": "10.0.0.9",
                            "Placement": {"AvailabilityZone": "us-east-1b"},
                            "Tags": [],
                        },
                    ],
                },
            ],
        },
        {"InstanceIds": [iid]},
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
    with Session(sqlite_engine) as session:
        node = register_ec2_instance(session, iid, ec2_client=client)
        session.commit()
        assert node.schedulable is False
        assert node.status == ExecutionNodeStatus.NOT_READY.value
    stubber.deactivate()
