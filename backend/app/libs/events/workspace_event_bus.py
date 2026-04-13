"""Lightweight in-process workspace event bus.

Design
------
When a workspace event is persisted to the DB, the writing thread (worker/service)
calls :func:`notify_workspace_event` to wake up SSE generator coroutines that are
waiting for that workspace.  SSE generators then immediately re-poll the DB instead
of sleeping for the full ``SSE_POLL_INTERVAL_SEC`` — reducing perceived latency from
~1 s to near-zero for live events.

Architecture
------------
- Uses per-workspace ``asyncio.Event`` objects stored in a module-level dict.
- Worker/service code (sync, runs in a thread-pool) signals the event loop via
  :func:`asyncio.get_event_loop().call_soon_threadsafe` (safe cross-thread signalling).
- SSE coroutines await their workspace event with a configurable fallback timeout
  so they still poll even when the event bus is unavailable (e.g. multi-process).

Multi-process note
------------------
This bus is in-process only.  In a multi-process deployment (e.g. ``gunicorn`` with
multiple workers) the worker process that commits the event will notify its own
in-process listeners.  Clients connected to a *different* API process will fall back
to periodic DB polling unchanged.  For single-process deployments (one ``uvicorn``
process with the built-in background worker) this gives real push-notification.

Thread-safety
-------------
The ``_listeners`` dict is only mutated by asyncio coroutines (via
:meth:`WorkspaceEventBus.subscribe` / :meth:`~WorkspaceEventBus.unsubscribe`) which
run on a single event-loop thread, so no mutex is needed for the dict itself.
The cross-thread notify path (``call_soon_threadsafe``) does not touch the dict.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections import defaultdict

_logger = logging.getLogger(__name__)

# Fallback poll interval (seconds) used by SSE generator when no notification arrives.
EVENT_BUS_WAIT_TIMEOUT_SEC: float = 2.0


class WorkspaceEventBus:
    """Per-workspace asyncio.Event registry for SSE push notification."""

    def __init__(self) -> None:
        # workspace_id → set of asyncio.Event objects (one per SSE connection)
        self._listeners: dict[int, set[asyncio.Event]] = defaultdict(set)
        # Reference to the running event loop; set via :meth:`attach_event_loop`.
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the event loop so cross-thread notifications work."""
        self._loop = loop

    def subscribe(self, workspace_id: int) -> asyncio.Event:
        """Create and register a new event for ``workspace_id``.

        Must be called from the event loop thread (SSE coroutine startup).
        """
        evt = asyncio.Event()
        self._listeners[workspace_id].add(evt)
        return evt

    def unsubscribe(self, workspace_id: int, event: asyncio.Event) -> None:
        """Remove a listener event.  Safe to call even if already removed."""
        listeners = self._listeners.get(workspace_id)
        if listeners is None:
            return
        listeners.discard(event)
        if not listeners:
            self._listeners.pop(workspace_id, None)

    def notify(self, workspace_id: int) -> None:
        """Signal all SSE listeners for ``workspace_id`` from the event-loop thread.

        Called after committing a workspace event to the DB.  If the event loop is not
        available (e.g. called before the app started), this is a no-op.
        """
        listeners = self._listeners.get(workspace_id)
        if not listeners:
            return
        for evt in list(listeners):
            evt.set()

    def notify_threadsafe(self, workspace_id: int) -> None:
        """Signal all SSE listeners from a *non-event-loop* thread (worker thread-pool).

        Uses ``call_soon_threadsafe`` so it is safe to call from sync code running in
        ``loop.run_in_executor``.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self.notify, workspace_id)
        except RuntimeError:
            # Loop may be shutting down — ignore.
            pass

    def active_listener_count(self, workspace_id: int) -> int:
        return len(self._listeners.get(workspace_id) or set())


# Module-level singleton — imported directly by services and routes.
_bus: WorkspaceEventBus | None = None


def get_event_bus() -> WorkspaceEventBus:
    """Return the module-level singleton :class:`WorkspaceEventBus`, creating it lazily."""
    global _bus
    if _bus is None:
        _bus = WorkspaceEventBus()
    return _bus


def notify_workspace_event(workspace_id: int) -> None:
    """Convenience helper: notify the singleton bus from any context.

    Detects whether the calling code is on the event-loop thread or a worker thread
    and routes to the appropriate notify method.

    This is the single call-site for publishing from sync worker/service code.
    """
    bus = get_event_bus()
    try:
        loop = asyncio.get_running_loop()
        # Running loop means we're inside async code on the event-loop thread.
        bus.notify(workspace_id)
        return
    except RuntimeError:
        pass
    # Not in an async context — use threadsafe cross-thread signal.
    bus.notify_threadsafe(workspace_id)
