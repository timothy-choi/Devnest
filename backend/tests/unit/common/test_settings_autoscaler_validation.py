"""Settings validation for autoscaler-related fields."""

from __future__ import annotations

from app.libs.common.config import Settings
from app.services.infrastructure_service.models import Ec2ProvisionRequest


def test_devnest_autoscaler_min_ec2_nodes_before_reclaim_coerces_below_two() -> None:
    s = Settings(
        database_url="sqlite://",
        devnest_autoscaler_min_ec2_nodes_before_reclaim=1,
    )
    assert s.devnest_autoscaler_min_ec2_nodes_before_reclaim == 2


def test_phase2_scale_out_autoscaler_defaults() -> None:
    s = Settings(database_url="sqlite://")
    assert s.devnest_autoscaler_enabled is True
    assert s.devnest_autoscaler_evaluate_only is False
    assert s.devnest_autoscaler_min_nodes == 1
    assert s.devnest_autoscaler_max_nodes == 10
    assert s.devnest_autoscaler_min_idle_slots == 1
    assert s.devnest_autoscaler_scale_out_cooldown_seconds == 300
    assert s.devnest_autoscaler_scale_in_cooldown_seconds == 900


def test_ec2_provision_request_reads_bootstrap_and_extra_tags_from_settings() -> None:
    s = Settings(
        database_url="sqlite://",
        aws_region="us-east-1",
        devnest_ec2_ami_id="ami-12345678",
        devnest_ec2_instance_type="t3.micro",
        devnest_ec2_subnet_id="subnet-12345678",
        devnest_ec2_security_group_ids="sg-12345678,sg-abcdef12",
        devnest_ec2_instance_profile="DevNestExecutionNodeProfile",
        devnest_ec2_user_data_b64="IyEvYmluL2Jhc2gKZWNobyBkZXZuZXN0Cg==",
        devnest_ec2_extra_tags="env=test,service=execution-node",
    )

    req = Ec2ProvisionRequest.from_settings(s)

    assert req.region == "us-east-1"
    assert req.security_group_ids == ["sg-12345678", "sg-abcdef12"]
    assert req.iam_instance_profile_name == "DevNestExecutionNodeProfile"
    assert req.user_data == "#!/bin/bash\necho devnest\n"
    assert req.extra_tags == {"env": "test", "service": "execution-node"}
