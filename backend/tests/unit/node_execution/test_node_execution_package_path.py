"""Shorter import path ``app.services.node_execution`` re-exports SSM provider."""

from __future__ import annotations

from app.services.node_execution.ssm_execution_provider import SsmExecutionProvider
from app.services.node_execution_service.ssm_execution_provider import (
    SsmExecutionProvider as CanonicalSsmExecutionProvider,
)


def test_node_execution_package_aliases_ssm_provider() -> None:
    assert SsmExecutionProvider is CanonicalSsmExecutionProvider
