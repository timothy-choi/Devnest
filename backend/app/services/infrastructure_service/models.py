"""Dataclasses for EC2 provisioning requests (explicit V1 configuration)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.libs.common.config import Settings, get_settings

from .errors import Ec2ProvisionConfigurationError


@dataclass
class Ec2ProvisionRequest:
    """Inputs for ``run_instances`` — prefer explicit fields over implicit defaults in production."""

    ami_id: str
    instance_type: str
    subnet_id: str
    security_group_ids: list[str]
    iam_instance_profile_name: str | None = None
    key_name: str | None = None
    region: str | None = None
    node_key: str | None = None
    name_tag: str | None = None
    execution_mode: str | None = None
    ssh_user: str | None = None
    extra_tags: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not (self.ami_id or "").strip():
            raise Ec2ProvisionConfigurationError("ami_id is required for EC2 provisioning")
        if not (self.subnet_id or "").strip():
            raise Ec2ProvisionConfigurationError("subnet_id is required for EC2 provisioning")
        if not self.security_group_ids:
            raise Ec2ProvisionConfigurationError(
                "security_group_ids must contain at least one security group for VPC instances",
            )
        if not (self.instance_type or "").strip():
            raise Ec2ProvisionConfigurationError("instance_type is required for EC2 provisioning")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Ec2ProvisionRequest:
        """Build a request from ``DEVNEST_EC2_*`` settings (may still fail :meth:`validate`)."""
        s = settings or get_settings()
        raw_sg = (s.devnest_ec2_security_group_ids or "").strip()
        sg_ids = [x.strip() for x in raw_sg.split(",") if x.strip()]
        prof = (s.devnest_ec2_instance_profile or "").strip() or None
        key = (s.devnest_ec2_key_name or "").strip() or None
        region = (s.aws_region or "").strip() or None
        return cls(
            ami_id=(s.devnest_ec2_ami_id or "").strip(),
            instance_type=(s.devnest_ec2_instance_type or "").strip() or "t3.medium",
            subnet_id=(s.devnest_ec2_subnet_id or "").strip(),
            security_group_ids=sg_ids,
            iam_instance_profile_name=prof,
            key_name=key,
            region=region,
        )
