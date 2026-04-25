"""FastAPI-integrated background job worker.

Runs the workspace job poll loop as an asyncio background task inside the
FastAPI process. Controlled by two environment variables:

    DEVNEST_WORKER_ENABLED=true             — opt-in; default false
    DEVNEST_WORKER_POLL_INTERVAL_SECONDS=5  — seconds between ticks; default 5
    DEVNEST_WORKER_BATCH_SIZE=5             — max jobs per tick; default 5

Lifecycle:
    start_background_worker()  — called inside the lifespan ``async with`` block
    stop_background_worker()   — called on shutdown (awaited; cancels the task)

The standalone ``python -m app.workers.workspace_job_poll_loop`` process
remains fully independent and unaffected by this module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.libs.observability.log_events import log_event, LogEvent

_logger = logging.getLogger(__name__)

# Module-level handle so stop_background_worker can cancel the running task.
_worker_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]


def in_process_workspace_worker_running() -> bool:
    """True when this API process started the in-process job loop and the task is still active."""
    t = _worker_task
    return t is not None and not t.done()


async def _run_one_tick(*, batch_size: int) -> int:
    """Execute one worker tick in the default thread-pool executor.

    Returns the number of jobs processed, or 0 on error (errors are logged,
    never propagated — the loop must not die due to transient failures).
    """
    import asyncio  # noqa: PLC0415 — imported here to avoid circular at module load

    loop = asyncio.get_running_loop()

    def _sync_tick() -> int:
        from app.libs.db.database import get_engine  # noqa: PLC0415
        from sqlmodel import Session  # noqa: PLC0415
        from app.workers.workspace_job_runner import (  # noqa: PLC0415
            execute_workspace_job_tick_with_default_orchestrator,
        )
        from app.workers.workspace_job_worker.worker import reclaim_stuck_running_jobs  # noqa: PLC0415

        engine = get_engine()

        # Reclaim jobs orphaned by a previous crashed worker before claiming new ones.
        try:
            reclaimed = reclaim_stuck_running_jobs(engine)
            if reclaimed:
                _logger.info("lifespan_worker_reclaimed_stuck_jobs", extra={"count": reclaimed})
        except Exception:
            _logger.warning("lifespan_worker_reclaim_error", exc_info=True)

        with Session(engine) as session:
            result = execute_workspace_job_tick_with_default_orchestrator(
                session,
                limit=batch_size,
            )
            return result.processed_count

    try:
        count: int = await loop.run_in_executor(None, _sync_tick)
        return count
    except Exception:
        _logger.warning("lifespan_worker_tick_error", exc_info=True)
        return 0


async def _poll_loop(*, poll_interval: float, batch_size: int) -> None:
    """Continuously poll the job queue until cancelled."""
    log_event(
        _logger,
        LogEvent.WORKSPACE_JOB_WORKER_STARTED,
        poll_interval_seconds=poll_interval,
        batch_size=batch_size,
    )
    try:
        while True:
            count = await _run_one_tick(batch_size=batch_size)
            if count > 0:
                log_event(_logger, LogEvent.WORKSPACE_JOB_WORKER_TICK, processed_count=count)
            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
    except asyncio.CancelledError:
        pass
    finally:
        log_event(_logger, LogEvent.WORKSPACE_JOB_WORKER_STOPPED)


def start_background_worker() -> Optional[asyncio.Task]:  # type: ignore[type-arg]
    """Start the background worker task if enabled.

    Must be called from an async context (e.g., inside FastAPI lifespan).
    Returns the created Task, or None if the worker is disabled.
    """
    global _worker_task

    from app.libs.common.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    enabled: bool = getattr(settings, "devnest_worker_enabled", False)

    if not enabled:
        _logger.info(
            "lifespan_worker_disabled",
            extra={"reason": "DEVNEST_WORKER_ENABLED is not set to true"},
        )
        return None

    poll_interval: float = float(
        getattr(settings, "devnest_worker_poll_interval_seconds", 5)
    )
    batch_size: int = int(getattr(settings, "devnest_worker_batch_size", 5))

    _worker_task = asyncio.create_task(
        _poll_loop(poll_interval=poll_interval, batch_size=batch_size),
        name="devnest-job-worker",
    )
    _logger.info(
        "lifespan_worker_started",
        extra={"poll_interval_seconds": poll_interval, "batch_size": batch_size},
    )
    return _worker_task


async def stop_background_worker() -> None:
    """Cancel the background worker task and wait for it to finish cleanly.

    Safe to call even if the worker was never started or already stopped.
    Waits up to 10 seconds for graceful cancellation before giving up.
    """
    global _worker_task

    if _worker_task is None or _worker_task.done():
        _worker_task = None
        return

    _worker_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(_worker_task), timeout=10.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        _worker_task = None
    _logger.info("lifespan_worker_shutdown_complete")
