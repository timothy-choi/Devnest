"""Probe layer exceptions. Prefer ``WorkspaceHealthResult`` / ``HealthIssue`` for bad health."""


class ProbeError(Exception):
    """Base for unexpected probe-runner failures (bugs, misconfiguration, unusable dependencies)."""


class ServiceProbeExecutionError(ProbeError):
    """Reserved for rare cases where a service probe cannot run; V1 ``DefaultProbeRunner`` returns ``ServiceProbeResult`` instead."""
