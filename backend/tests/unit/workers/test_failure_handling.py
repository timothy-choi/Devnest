"""Unit tests: workspace job failure classification helpers."""

from __future__ import annotations

import pytest

from app.services.placement_service.errors import NoSchedulableNodeError, PlacementError
from app.services.workspace_service.models.enums import FailureStage, WorkspaceJobType
from app.workers.workspace_job_worker.failure_handling import (
    classify_placement_error,
    classify_reconcile_failure,
    lifecycle_result_failure_retryable,
    orchestrator_exception_retryable,
)


def test_classify_placement_no_schedulable_is_capacity() -> None:
    exc = NoSchedulableNodeError("no nodes")
    stage, retryable = classify_placement_error(exc)
    assert stage == FailureStage.CAPACITY
    assert retryable is True


def test_classify_placement_generic_is_placement() -> None:
    exc = PlacementError("bad config")
    stage, retryable = classify_placement_error(exc)
    assert stage == FailureStage.PLACEMENT
    assert retryable is True


@pytest.mark.parametrize(
    "message,expected_stage,retryable",
    [
        ("reconcile:workspace_busy (status=STARTING)", FailureStage.UNKNOWN, False),
        ("reconcile:unsupported_workspace_status:X", FailureStage.UNKNOWN, False),
        ("reconcile:gateway_list_failed:timeout", FailureStage.PROXY, True),
        ("reconcile:runtime_not_healthy", FailureStage.CONTAINER, True),
        ("reconcile:health_check_failed:oops", FailureStage.CONTAINER, True),
        ("reconcile:stop_failed:bad", FailureStage.CONTAINER, True),
        ("something odd", FailureStage.UNKNOWN, True),
    ],
)
def test_classify_reconcile_failure(
    message: str,
    expected_stage: FailureStage,
    retryable: bool,
) -> None:
    stage, rb = classify_reconcile_failure(message)
    assert stage == expected_stage
    assert rb is retryable


def test_lifecycle_result_retry_types() -> None:
    assert lifecycle_result_failure_retryable(WorkspaceJobType.CREATE.value) is True
    assert lifecycle_result_failure_retryable(WorkspaceJobType.STOP.value) is False


def test_orchestrator_exception_retry_includes_reconcile() -> None:
    assert orchestrator_exception_retryable(WorkspaceJobType.RECONCILE_RUNTIME.value) is True
    assert orchestrator_exception_retryable(WorkspaceJobType.STOP.value) is False
