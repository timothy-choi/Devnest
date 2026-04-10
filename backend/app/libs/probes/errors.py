"""Probe layer exceptions. Prefer ``WorkspaceHealthResult`` / ``HealthIssue`` for bad health."""


class ProbeError(Exception):
    """Base for unexpected probe-runner failures (bugs, misconfiguration, unusable dependencies)."""


class ServiceProbeExecutionError(ProbeError):
    """The service reachability check could not run (e.g. I/O error); not an ``unhealthy`` outcome."""
