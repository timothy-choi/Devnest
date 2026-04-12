"""Infrastructure providers (EC2 registry + helpers for lifecycle sync)."""

from .ec2_provider import (
    Ec2InstanceDescription,
    build_ec2_client,
    compute_status_schedulable_after_ec2_sync,
    describe_ec2_instance,
    ec2_instance_type_capacity,
    list_ec2_instances,
    register_ec2_instance,
    sync_ec2_instances,
)
from .errors import Ec2InstanceNotFoundError, Ec2InvalidInstanceIdError, Ec2ProviderError, ProviderError

__all__ = [
    "Ec2InstanceDescription",
    "Ec2InstanceNotFoundError",
    "Ec2InvalidInstanceIdError",
    "Ec2ProviderError",
    "ProviderError",
    "build_ec2_client",
    "compute_status_schedulable_after_ec2_sync",
    "describe_ec2_instance",
    "ec2_instance_type_capacity",
    "list_ec2_instances",
    "register_ec2_instance",
    "sync_ec2_instances",
]
