"""Unit tests: :class:`~app.services.infrastructure_service.models.Ec2ProvisionRequest`."""

from __future__ import annotations

import pytest

from app.libs.common.config import get_settings
from app.services.infrastructure_service.errors import Ec2ProvisionConfigurationError
from app.services.infrastructure_service.lifecycle import _run_instances_params
from app.services.infrastructure_service.models import Ec2ProvisionRequest, build_default_amazon_linux_2023_user_data


def test_validate_requires_ami_subnet_sg() -> None:
    with pytest.raises(Ec2ProvisionConfigurationError, match="ami_id"):
        Ec2ProvisionRequest(
            ami_id="",
            instance_type="t3.micro",
            subnet_id="subnet-abc",
            security_group_ids=["sg-1"],
        ).validate()
    with pytest.raises(Ec2ProvisionConfigurationError, match="subnet_id"):
        Ec2ProvisionRequest(
            ami_id="ami-x",
            instance_type="t3.micro",
            subnet_id="",
            security_group_ids=["sg-1"],
        ).validate()
    with pytest.raises(Ec2ProvisionConfigurationError, match="security_group_ids"):
        Ec2ProvisionRequest(
            ami_id="ami-x",
            instance_type="t3.micro",
            subnet_id="subnet-1",
            security_group_ids=[],
        ).validate()


def test_from_settings_parses_security_groups(monkeypatch) -> None:
    monkeypatch.setenv("DEVNEST_EC2_SECURITY_GROUP_IDS", "sg-aaa, sg-bbb")
    monkeypatch.setenv("DEVNEST_EC2_AMI_ID", "ami-12345")
    monkeypatch.setenv("DEVNEST_EC2_SUBNET_ID", "subnet-xyz")
    get_settings.cache_clear()
    req = Ec2ProvisionRequest.from_settings()
    assert req.security_group_ids == ["sg-aaa", "sg-bbb"]
    assert req.ami_id == "ami-12345"
    assert req.subnet_id == "subnet-xyz"
    get_settings.cache_clear()


def test_run_instances_params_structure() -> None:
    req = Ec2ProvisionRequest(
        ami_id="ami-abc",
        instance_type="t3.small",
        subnet_id="subnet-1",
        security_group_ids=["sg-1"],
        iam_instance_profile_name="profile-a",
        key_name="my-key",
        extra_tags={"Project": "devnest"},
    )
    settings = get_settings()
    params = _run_instances_params(req, settings)
    assert params["ImageId"] == "ami-abc"
    assert params["InstanceType"] == "t3.small"
    assert params["SubnetId"] == "subnet-1"
    assert params["SecurityGroupIds"] == ["sg-1"]
    assert params["IamInstanceProfile"] == {"Name": "profile-a"}
    assert params["KeyName"] == "my-key"
    tag_specs = params["TagSpecifications"][0]["Tags"]
    keys = {t["Key"]: t["Value"] for t in tag_specs}
    assert keys["Project"] == "devnest"
    prefix = (settings.devnest_ec2_tag_prefix or "devnest").strip() or "devnest"
    assert keys[f"{prefix}:managed"] == "true"


def test_run_instances_params_include_generated_user_data() -> None:
    user_data = build_default_amazon_linux_2023_user_data(
        node_key="ec2-autoscale-test123",
        internal_api_base_url="http://api.internal:8000",
        internal_api_key="secret-not-logged",
        workspace_projects_base="/var/lib/devnest/workspace-projects",
        heartbeat_interval_seconds=30,
    )
    req = Ec2ProvisionRequest(
        ami_id="ami-abc",
        instance_type="t3.small",
        subnet_id="subnet-1",
        security_group_ids=["sg-1"],
        node_key="ec2-autoscale-test123",
        user_data=user_data,
    )

    params = _run_instances_params(req, get_settings())

    assert params["UserData"] == user_data
    assert "dnf install -y docker" in params["UserData"]
    assert "dnf install -y docker curl" not in params["UserData"]
    assert "NODE_KEY=ec2-autoscale-test123" in params["UserData"]
    assert "devnest-node-heartbeat.service" in params["UserData"]
    assert "/opt/devnest" in params["UserData"]
    assert "/var/lib/devnest/workspace-projects" in params["UserData"]
