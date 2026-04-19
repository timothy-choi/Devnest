"""Stable V1 issue codes for ``HealthIssue.code`` (probe runner; read-only).

``HealthIssue.component`` conventions: ``runtime`` (container inspect), ``topology`` (adapter
health semantics), ``service`` (TCP reachability), ``probe`` (invalid probe input or runner I/O).
"""

from enum import Enum


class ProbeIssueCode(str, Enum):
    """
    String enum values equal the wire/log code (stable for APIs and metrics).

    Use with ``HealthIssue``; do not encode repair actions here.
    """

    # Runtime / container
    RUNTIME_CONTAINER_MISSING = "RUNTIME_CONTAINER_MISSING"
    RUNTIME_NOT_RUNNING = "RUNTIME_NOT_RUNNING"
    RUNTIME_CONTAINER_STATE_UNKNOWN = "RUNTIME_CONTAINER_STATE_UNKNOWN"

    # Topology runtime + attachment
    TOPOLOGY_UNHEALTHY = "TOPOLOGY_UNHEALTHY"
    TOPOLOGY_ATTACHMENT_MISSING = "TOPOLOGY_ATTACHMENT_MISSING"
    TOPOLOGY_WORKSPACE_IP_MISSING = "TOPOLOGY_WORKSPACE_IP_MISSING"
    TOPOLOGY_INTERNAL_ENDPOINT_MISSING = "TOPOLOGY_INTERNAL_ENDPOINT_MISSING"

    # Service reachability
    SERVICE_UNREACHABLE = "SERVICE_UNREACHABLE"
    SERVICE_TIMEOUT = "SERVICE_TIMEOUT"
    SERVICE_CONNECT_ERROR = "SERVICE_CONNECT_ERROR"
    # HTTP-level readiness: TCP connected but service returned an error or is still initialising.
    SERVICE_HTTP_NOT_READY = "SERVICE_HTTP_NOT_READY"

    # Probe runner / execution (host TCP probe uses ``nc`` + ``timeout`` from the worker/API image)
    PROBE_EXECUTION_FAILED = "PROBE_EXECUTION_FAILED"
    PROBE_RUNTIME_BINARY_MISSING = "PROBE_RUNTIME_BINARY_MISSING"
