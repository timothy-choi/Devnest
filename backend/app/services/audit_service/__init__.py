"""Durable append-only audit log for DevNest V1.

Records who did what, when, to what resource, and with what outcome.

TODO: Route audit rows to an external SIEM or tamper-proof log store for regulatory compliance.
TODO: Add audit log rotation / archival policy for long-running deployments.
"""

from .models import AuditLog
from .service import record_audit

__all__ = ["AuditLog", "record_audit"]
