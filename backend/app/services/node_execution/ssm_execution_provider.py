"""
SSM-backed shell / ``docker`` helpers on EC2 (re-export).

Implemented in :mod:`app.services.node_execution_service.ssm_execution_provider`.
"""

from app.services.node_execution_service.ssm_execution_provider import SsmExecutionProvider

__all__ = ["SsmExecutionProvider"]
