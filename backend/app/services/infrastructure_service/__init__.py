"""EC2 provisioning and execution-node lifecycle (control plane)."""

from .errors import Ec2ProvisionConfigurationError, InfrastructureError, NodeLifecycleError
from .lifecycle import (
    deregister_node,
    mark_node_draining,
    provision_ec2_node,
    register_existing_ec2_node,
    sync_node_state,
    terminate_ec2_node,
)
from .models import Ec2ProvisionRequest
from .ssm_readiness import is_instance_ssm_online

__all__ = [
    "Ec2ProvisionConfigurationError",
    "Ec2ProvisionRequest",
    "InfrastructureError",
    "NodeLifecycleError",
    "deregister_node",
    "is_instance_ssm_online",
    "mark_node_draining",
    "provision_ec2_node",
    "register_existing_ec2_node",
    "sync_node_state",
    "terminate_ec2_node",
]
