"""Unit tests for the FastAPI lifespan background job worker.

Tests:
    - Worker does not start when DEVNEST_WORKER_ENABLED is false (default).
    - Worker creates an asyncio Task when enabled.
    - Worker Task is cancelled and awaited on stop_background_worker().
    - Poll loop handles tick errors gracefully (does not crash the loop).
    - stop_background_worker() is a no-op when the worker was never started.

Uses asyncio.run() for synchronous test compatibility (no pytest-asyncio required).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    """Return a minimal mock Settings object with worker fields."""
    s = MagicMock()
    s.devnest_worker_enabled = overrides.get("devnest_worker_enabled", False)
    s.devnest_worker_poll_interval_seconds = overrides.get(
        "devnest_worker_poll_interval_seconds", 5
    )
    s.devnest_worker_batch_size = overrides.get("devnest_worker_batch_size", 5)
    return s


def _reset_worker():
    import app.workers.lifespan_worker as lw

    lw._worker_task = None


# ---------------------------------------------------------------------------
# start_background_worker — disabled
# ---------------------------------------------------------------------------


def test_start_worker_disabled_returns_none():
    """start_background_worker() returns None when worker is disabled."""
    _reset_worker()

    import app.workers.lifespan_worker as lw

    async def _run():
        with patch(
            # Patch where get_settings is used (imported inside the function)
            "app.libs.common.config.get_settings",
            return_value=_make_settings(devnest_worker_enabled=False),
        ):
            result = lw.start_background_worker()
        assert result is None
        assert lw._worker_task is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# start_background_worker — enabled
# ---------------------------------------------------------------------------


def test_start_worker_enabled_creates_task():
    """start_background_worker() creates an asyncio Task when enabled."""
    _reset_worker()

    import app.workers.lifespan_worker as lw

    tick_event = asyncio.Event()

    async def _fake_poll_loop(*, poll_interval: float, batch_size: int) -> None:
        tick_event.set()
        await asyncio.sleep(9999)

    async def _run():
        with (
            patch(
                "app.libs.common.config.get_settings",
                return_value=_make_settings(
                    devnest_worker_enabled=True,
                    devnest_worker_poll_interval_seconds=60,
                    devnest_worker_batch_size=3,
                ),
            ),
            patch("app.workers.lifespan_worker._poll_loop", side_effect=_fake_poll_loop),
        ):
            task = lw.start_background_worker()
            assert task is not None
            assert isinstance(task, asyncio.Task)
            await asyncio.wait_for(tick_event.wait(), timeout=2.0)

        # Clean up
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        lw._worker_task = None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# stop_background_worker — no-op when not started
# ---------------------------------------------------------------------------


def test_stop_worker_no_op_when_not_started():
    """stop_background_worker() is a no-op when _worker_task is None."""
    _reset_worker()

    import app.workers.lifespan_worker as lw

    async def _run():
        await lw.stop_background_worker()
        assert lw._worker_task is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# stop_background_worker — cancels running task
# ---------------------------------------------------------------------------


def test_stop_worker_cancels_running_task():
    """stop_background_worker() cancels the task and clears the module reference."""
    _reset_worker()

    import app.workers.lifespan_worker as lw

    async def _long_running() -> None:
        await asyncio.sleep(9999)

    async def _run():
        task = asyncio.create_task(_long_running(), name="test-worker")
        # Let the task start so it is suspended at the sleep().
        await asyncio.sleep(0)
        lw._worker_task = task

        await lw.stop_background_worker()

        assert lw._worker_task is None
        assert task.done()
        assert task.cancelled()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# stop_background_worker — already-finished task
# ---------------------------------------------------------------------------


def test_stop_worker_already_done_task():
    """stop_background_worker() handles a task that has already completed."""
    _reset_worker()

    import app.workers.lifespan_worker as lw

    async def _instant() -> None:
        return

    async def _run():
        task = asyncio.create_task(_instant())
        await task  # Let it finish naturally.
        lw._worker_task = task
        # Should not raise even though the task is done.
        await lw.stop_background_worker()
        assert lw._worker_task is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# _run_one_tick error handling
# ---------------------------------------------------------------------------


def test_run_one_tick_returns_zero_on_exception():
    """_run_one_tick returns 0 and logs when the executor raises."""
    import app.workers.lifespan_worker as lw

    async def _run():
        loop = asyncio.get_event_loop()
        original = loop.run_in_executor

        async def _bad_executor(executor, fn, *args):
            raise RuntimeError("DB exploded")

        with patch.object(loop, "run_in_executor", side_effect=RuntimeError("DB exploded")):
            count = await lw._run_one_tick(batch_size=5)

        assert count == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# _poll_loop — exits on CancelledError
# ---------------------------------------------------------------------------


def test_poll_loop_exits_on_cancelled_error():
    """_poll_loop exits cleanly when cancelled."""
    import app.workers.lifespan_worker as lw

    async def _run():
        with patch(
            "app.workers.lifespan_worker._run_one_tick",
            new_callable=AsyncMock,
            return_value=0,
        ):
            task = asyncio.create_task(
                lw._poll_loop(poll_interval=0.01, batch_size=1)
            )
            # Allow one iteration.
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        assert task.done()

    asyncio.run(_run())
