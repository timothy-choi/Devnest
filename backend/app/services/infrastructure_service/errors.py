"""Control-plane infrastructure lifecycle errors (not HTTP-mapped here)."""


class InfrastructureError(Exception):
    """Base for EC2 provisioning / node lifecycle failures."""


class Ec2ProvisionConfigurationError(InfrastructureError):
    """Missing or invalid provisioning configuration (AMI, subnet, security groups, etc.)."""


class NodeLifecycleError(InfrastructureError):
    """Illegal node lifecycle transition or unsupported operation for this node type."""
