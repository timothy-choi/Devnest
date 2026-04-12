"""
Compatibility entry point for ``app.services.node_execution``.

Canonical implementation lives in :mod:`app.services.node_execution_service` (bundle resolution,
SSM/SSH/local Docker). Import from here only when you want the shorter package path; new code may
prefer ``node_execution_service`` directly.
"""

from app.services.node_execution_service.ssm_execution_provider import SsmExecutionProvider

__all__ = ["SsmExecutionProvider"]
