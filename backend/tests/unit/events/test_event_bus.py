"""Tests for WorkspaceEventBus (Task 4: SSE event delivery improvement)."""

from __future__ import annotations

import asyncio
import pytest

from app.libs.events.workspace_event_bus import WorkspaceEventBus, notify_workspace_event


class TestWorkspaceEventBus:

    def test_subscribe_returns_event(self) -> None:
        bus = WorkspaceEventBus()
        # Must be called from an event loop context for asyncio.Event creation.
        async def _run():
            evt = bus.subscribe(1)
            assert evt is not None
            assert isinstance(evt, asyncio.Event)
        asyncio.run(_run())

    def test_notify_sets_listener_event(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            evt = bus.subscribe(42)
            assert not evt.is_set()
            bus.notify(42)
            assert evt.is_set()

        asyncio.run(_run())

    def test_notify_only_targets_correct_workspace(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            evt_a = bus.subscribe(1)
            evt_b = bus.subscribe(2)
            bus.notify(1)
            assert evt_a.is_set()
            assert not evt_b.is_set()

        asyncio.run(_run())

    def test_unsubscribe_removes_listener(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            evt = bus.subscribe(10)
            bus.unsubscribe(10, evt)
            bus.notify(10)
            assert not evt.is_set()  # Should not be set after unsubscribe.

        asyncio.run(_run())

    def test_active_listener_count(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            assert bus.active_listener_count(5) == 0
            e1 = bus.subscribe(5)
            e2 = bus.subscribe(5)
            assert bus.active_listener_count(5) == 2
            bus.unsubscribe(5, e1)
            assert bus.active_listener_count(5) == 1
            bus.unsubscribe(5, e2)
            assert bus.active_listener_count(5) == 0

        asyncio.run(_run())

    def test_multiple_subscribers_all_notified(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            evts = [bus.subscribe(99) for _ in range(5)]
            bus.notify(99)
            assert all(e.is_set() for e in evts)

        asyncio.run(_run())

    def test_notify_before_any_subscriber_is_noop(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            bus.notify(7)  # No listeners — should not raise.
            evt = bus.subscribe(7)
            assert not evt.is_set()  # Notification before subscription is not replayed.

        asyncio.run(_run())

    def test_wait_resolves_when_notified(self) -> None:
        """SSE generator pattern: wait resolves before timeout when notify fires."""
        bus = WorkspaceEventBus()

        async def _run():
            evt = bus.subscribe(100)

            async def _waiter():
                await asyncio.wait_for(evt.wait(), timeout=1.0)
                return True

            async def _notifier():
                await asyncio.sleep(0.01)
                bus.notify(100)

            result, _ = await asyncio.gather(_waiter(), _notifier())
            assert result is True

        asyncio.run(_run())

    def test_wait_times_out_without_notification(self) -> None:
        bus = WorkspaceEventBus()

        async def _run():
            evt = bus.subscribe(200)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(evt.wait(), timeout=0.05)

        asyncio.run(_run())

    def test_notify_threadsafe_signals_listeners(self) -> None:
        """notify_threadsafe (cross-thread signal) should wake SSE generator."""
        import threading  # noqa: PLC0415

        bus = WorkspaceEventBus()

        async def _run():
            loop = asyncio.get_running_loop()
            bus.attach_event_loop(loop)
            evt = bus.subscribe(300)

            def _thread_notify():
                import time  # noqa: PLC0415
                time.sleep(0.02)
                bus.notify_threadsafe(300)

            t = threading.Thread(target=_thread_notify)
            t.start()
            await asyncio.wait_for(evt.wait(), timeout=1.0)
            assert evt.is_set()
            t.join()

        asyncio.run(_run())
