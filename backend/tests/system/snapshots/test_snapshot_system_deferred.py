"""System-tier snapshot E2E (real Docker orchestrator + worker) — deferred.

Full lifecycle (create workspace → RUNNING → snapshot → STOP → restore → START) belongs in the same
class of tests as ``tests/system/workspace/test_workspace_control_plane_system.py``. Run when CI
provides Postgres + Docker + snapshot storage root.

TODO: Implement ``test_snapshot_full_lifecycle_system`` mirroring control-plane fixtures once
operator demand justifies the maintenance cost.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.system]


@pytest.mark.skip(reason="Deferred: Docker + Postgres snapshot E2E (see module docstring).")
def test_snapshot_full_lifecycle_system_placeholder() -> None:
    assert False
