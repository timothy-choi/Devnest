"""Provider-layer errors (EC2 registry; no user-facing HTTP mapping here)."""


class ProviderError(Exception):
    """Base for infrastructure provider failures."""


class Ec2ProviderError(ProviderError):
    """EC2 describe/registry operation failed."""


class Ec2InstanceNotFoundError(Ec2ProviderError):
    """No EC2 instance with the given id in the configured region/account."""


class Ec2InvalidInstanceIdError(Ec2ProviderError):
    """Malformed EC2 instance id (before any AWS call). Map to HTTP 400 in admin APIs."""
