"""Unit tests: workspace job config parsing for cgroup overrides."""

from __future__ import annotations

import pytest

from app.workers.workspace_job_worker.worker import _workspace_job_runtime_overrides_from_config


def test_missing_keys_yield_none_tuple() -> None:
    assert _workspace_job_runtime_overrides_from_config({}) == (None, None, None)


def test_present_valid_overrides() -> None:
    cpu, mem, pids = _workspace_job_runtime_overrides_from_config(
        {"cpu_limit_cores": 2.5, "memory_limit_mib": 2048, "pids_limit": 1024},
    )
    assert cpu == 2.5
    assert mem == 2048
    assert pids == 1024


def test_invalid_cpu_raises() -> None:
    with pytest.raises(ValueError, match="cpu_limit_cores"):
        _workspace_job_runtime_overrides_from_config({"cpu_limit_cores": "not-a-float"})


def test_invalid_pids_raises() -> None:
    with pytest.raises(ValueError, match="pids_limit"):
        _workspace_job_runtime_overrides_from_config({"pids_limit": -3})
