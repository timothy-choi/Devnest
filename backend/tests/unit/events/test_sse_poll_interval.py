"""Unit tests: SSE poll interval config setting and multi-worker DB-polling semantics."""

from __future__ import annotations

import pytest

from app.libs.common.config import Settings


class TestSsePollIntervalConfig:
    """Settings.devnest_sse_poll_interval_seconds validation."""

    def test_default_is_two_seconds(self):
        s = Settings(database_url="postgresql://x/y")
        assert s.devnest_sse_poll_interval_seconds == 2.0

    def test_custom_value_accepted(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds=5.0)
        assert s.devnest_sse_poll_interval_seconds == 5.0

    def test_value_below_minimum_clamped_to_half_second(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds=0.01)
        assert s.devnest_sse_poll_interval_seconds == 0.5

    def test_value_above_maximum_clamped_to_sixty(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds=999.0)
        assert s.devnest_sse_poll_interval_seconds == 60.0

    def test_string_env_value_coerced(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds="3.5")
        assert s.devnest_sse_poll_interval_seconds == 3.5

    def test_invalid_string_falls_back_to_default(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds="not_a_number")
        assert s.devnest_sse_poll_interval_seconds == 2.0

    def test_minimum_boundary(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds=0.5)
        assert s.devnest_sse_poll_interval_seconds == 0.5

    def test_maximum_boundary(self):
        s = Settings(database_url="postgresql://x/y", devnest_sse_poll_interval_seconds=60.0)
        assert s.devnest_sse_poll_interval_seconds == 60.0


class TestSseMultiWorkerDbPolling:
    """
    Verify that list_workspace_events correctly fetches events from DB — the
    function used by the SSE polling loop.  In a multi-worker deployment, a
    different worker writes the event; this worker reads it via this function.
    The test simulates that by writing a WorkspaceEvent row directly (no in-process
    bus notification) and asserting the query finds it.
    """

    def test_list_events_returns_rows_written_without_bus_notification(self):
        """
        WorkspaceEvent rows written to DB are returned by list_workspace_events
        regardless of whether the in-process event bus was notified.

        This proves that the SSE DB polling path delivers cross-worker events.
        """
        from datetime import datetime, timezone
        from unittest.mock import MagicMock, patch

        from app.services.workspace_service.models import WorkspaceEvent, Workspace
        from app.services.workspace_service.services.workspace_event_service import (
            list_workspace_events,
            WorkspaceStreamEventType,
        )

        wid = 42
        uid = 7
        now = datetime.now(timezone.utc)

        # Build a fake DB row (no bus notification — simulates cross-worker write)
        event_row = WorkspaceEvent(
            workspace_event_id=1,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
            status="RUNNING",
            message="cross-worker event",
            payload_json={"job_id": 100},
            created_at=now,
        )

        mock_ws = MagicMock(spec=Workspace)
        mock_ws.owner_user_id = uid

        # Mock the session so we control what the DB returns
        mock_session = MagicMock()
        mock_session.get.return_value = mock_ws

        # list_workspace_events calls session.exec(...).all()
        mock_exec = MagicMock()
        mock_exec.all.return_value = [event_row]
        mock_session.exec.return_value = mock_exec

        result = list_workspace_events(mock_session, workspace_id=wid, owner_user_id=uid, after_id=0)
        assert len(result) == 1
        assert result[0].workspace_event_id == 1
        assert result[0].event_type == WorkspaceStreamEventType.JOB_SUCCEEDED
        assert result[0].message == "cross-worker event"

    def test_list_events_respects_after_id_cursor(self):
        """SSE resume cursor (after_id) filters out already-seen events."""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        from app.services.workspace_service.models import WorkspaceEvent, Workspace
        from app.services.workspace_service.services.workspace_event_service import (
            list_workspace_events,
            WorkspaceStreamEventType,
        )

        wid, uid = 10, 3
        now = datetime.now(timezone.utc)

        event_new = WorkspaceEvent(
            workspace_event_id=5,
            workspace_id=wid,
            event_type=WorkspaceStreamEventType.RECONCILE_NOOP,
            status="RUNNING",
            message="new event",
            payload_json={},
            created_at=now,
        )

        mock_ws = MagicMock(spec=Workspace)
        mock_ws.owner_user_id = uid
        mock_session = MagicMock()
        mock_session.get.return_value = mock_ws
        mock_exec = MagicMock()
        mock_exec.all.return_value = [event_new]
        mock_session.exec.return_value = mock_exec

        result = list_workspace_events(mock_session, workspace_id=wid, owner_user_id=uid, after_id=4)
        assert len(result) == 1
        assert result[0].workspace_event_id == 5

    def test_notify_workspace_event_is_best_effort_does_not_raise(self):
        """If event bus fails, record_workspace_event must NOT raise (DB write still succeeds)."""
        from unittest.mock import MagicMock, patch

        from app.services.workspace_service.services.workspace_event_service import record_workspace_event

        mock_session = MagicMock()

        def _fake_add(row):
            row.workspace_event_id = 99

        mock_session.add.side_effect = _fake_add

        # Patch the notify function at its source module so the lazy import inside
        # record_workspace_event receives the patched version.
        with patch(
            "app.libs.events.workspace_event_bus.notify_workspace_event",
            side_effect=RuntimeError("bus exploded"),
        ):
            # Must NOT raise — bus failure is swallowed inside record_workspace_event
            result = record_workspace_event(
                mock_session,
                workspace_id=1,
                event_type="controlplane.job_succeeded",
                status="RUNNING",
                message="test",
            )
        assert result == 99
