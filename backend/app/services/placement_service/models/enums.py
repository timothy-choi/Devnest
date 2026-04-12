"""Enums for execution node registry (V1; EC2-backed agents later)."""

from enum import Enum


class ExecutionNodeProviderType(str, Enum):
    """Where the node process runs today; maps to cloud provider later."""

    LOCAL = "local"
    EC2 = "ec2"
    UNSPECIFIED = "unspecified"


class ExecutionNodeStatus(str, Enum):
    """Liveness / scheduling gate for the placement policy."""

    READY = "READY"
    NOT_READY = "NOT_READY"
    DRAINING = "DRAINING"


class ExecutionNodeExecutionMode(str, Enum):
    """How the worker reaches Docker + node-local topology commands for this node."""

    LOCAL_DOCKER = "local_docker"
    SSH_DOCKER = "ssh_docker"
