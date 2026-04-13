"""FastAPI-integrated background reconcile loop.

When enabled, this module enqueues ``RECONCILE_RUNTIME`` jobs for workspaces whose
status falls within the configured target set (default: ``RUNNING,ERROR``).

Configuration (environment / .env):
    DEVNEST_RECONCILE_ENABLED=true                  — opt-in; default false
    DEVNEST_RECONCILE_INTERVAL_SECONDS=30           — tick cadence; floor 10s
    DEVNEST_RECONCILE_BATCH_SIZE=10                 — max workspaces per tick
    DEVNEST_RECONCILE_TARGET_STATUSES=RUNNING,ERROR — comma-separated statuses

Lifecycle:
    start_reconcile_loop()  — call inside FastAPI lifespan
    stop_reconcile_loop()   — call on shutdown (awaited)

Design:
    - Each tick runs in the default thread-pool executor (sync ORM code).
    - ``enqueue_reconcile_runtime_job`` contains the lease/duplicate check
      (Task 2), so enqueueing is idempotent under concurrent loops or manual
      operator reconcile triggers.
    - Errors during a single workspace enqueue are logged and skipped; the loop
      continues with the next workspace.
    - Graceful shutdown: asyncio.CancelledError is caught; the loop exits cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.libs.observability.log_events import LogEvent, log_event

_logger = logging.getLogger(__name__)

_reconcile_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]


def _run_reconcile_tick_sync(*, batch_size: int, target_statuses: list[str]) -> int:
    """Execute one reconcile tick synchronously (runs in thread-pool).

    Returns the number of reconcile jobs successfully enqueued.
    """
    from app.libs.db.database import get_engine  # noqa: PLC0415
    from sqlmodel import Session, select  # noqa: PLC0415
    from app.services.workspace_service.models import Workspace  # noqa: PLC0415
    from app.services.workspace_service.services.workspace_intent_service import (  # noqa: PLC0415
        enqueue_reconcile_runtime_job,
    )
    from app.services.workspace_service.errors import (  # noqa: PLC0415
        WorkspaceBusyError,
        WorkspaceInvalidStateError,
    )

    engine = get_engine()
    enqueued = 0

    # Fetch candidate workspaces in a read-only session.
    with Session(engine) as read_session:
        stmt = (
            select(Workspace.workspace_id)  # type: ignore[arg-type]
            .where(Workspace.status.in_(target_statuses))  # type: ignore[attr-defined]
            .limit(batch_size)
        )
        rows = read_session.exec(stmt).all()
        candidate_ids: list[int] = [int(r) for r in rows]

    if not candidate_ids:
        return 0

    for ws_id in candidate_ids:
        try:
            with Session(engine) as session:
                enqueue_reconcile_runtime_job(session, workspace_id=ws_id)
            enqueued += 1
        except WorkspaceBusyError as exc:
            # Lease held or workspace is busy — expected, skip silently.
            log_event(
                _logger,
                LogEvent.RECONCILE_LOOP_ENQUEUE_SKIPPED,
                level=logging.DEBUG,
                workspace_id=ws_id,
                reason=str(exc)[:128],
            )
        except WorkspaceInvalidStateError as exc:
            # Workspace moved into a non-reconcilable status — skip.
            log_event(
                _logger,
                LogEvent.RECONCILE_LOOP_ENQUEUE_SKIPPED,
                level=logging.DEBUG,
                workspace_id=ws_id,
                reason=str(exc)[:128],
            )
        except Exception:
            _logger.warning(
                "reconcile_loop_enqueue_error",
                extra={"workspace_id": ws_id},
                exc_info=True,
            )

    return enqueued


async def _reconcile_loop(
    *,
    poll_interval: float,
    batch_size: int,
    target_statuses: list[str],
) -> None:
    """Continuously enqueue reconcile jobs until cancelled."""
    log_event(
        _logger,
        LogEvent.RECONCILE_LOOP_STARTED,
        poll_interval_seconds=poll_interval,
        batch_size=batch_size,
        target_statuses=target_statuses,
    )
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                count: int = await loop.run_in_executor(
                    None,
                    lambda: _run_reconcile_tick_sync(
                        batch_size=batch_size,
                        target_statuses=target_statuses,
                    ),
                )
                if count > 0:
                    log_event(
                        _logger,
                        LogEvent.RECONCILE_LOOP_TICK,
                        enqueued_count=count,
                    )
            except Exception:
                log_event(
                    _logger,
                    LogEvent.RECONCILE_LOOP_TICK_ERROR,
                    exc_info=True,
                )
            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
    except asyncio.CancelledError:
        pass
    finally:
        log_event(_logger, LogEvent.RECONCILE_LOOP_STOPPED)


def start_reconcile_loop() -> Optional[asyncio.Task]:  # type: ignore[type-arg]
    """Start the background reconcile loop task if enabled.

    Must be called from an async context (inside FastAPI lifespan).
    Returns the created Task, or None if reconcile is disabled.
    """
    global _reconcile_task

    from app.libs.common.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if not getattr(settings, "devnest_reconcile_enabled", False):
        _logger.info(
            "reconcile_loop_disabled",
            extra={"reason": "DEVNEST_RECONCILE_ENABLED is not true"},
        )
        return None

    poll_interval = float(getattr(settings, "devnest_reconcile_interval_seconds", 30))
    batch_size = int(getattr(settings, "devnest_reconcile_batch_size", 10))
    raw_statuses = str(getattr(settings, "devnest_reconcile_target_statuses", "RUNNING,ERROR"))
    target_statuses = [s.strip().upper() for s in raw_statuses.split(",") if s.strip()]
    if not target_statuses:
        target_statuses = ["RUNNING", "ERROR"]

    _reconcile_task = asyncio.create_task(
        _reconcile_loop(
            poll_interval=poll_interval,
            batch_size=batch_size,
            target_statuses=target_statuses,
        ),
        name="devnest-reconcile-loop",
    )
    _logger.info(
        "reconcile_loop_started",
        extra={
            "poll_interval_seconds": poll_interval,
            "batch_size": batch_size,
            "target_statuses": target_statuses,
        },
    )
    return _reconcile_task


async def stop_reconcile_loop() -> None:
    """Cancel the reconcile loop task and wait for clean shutdown.

    Safe to call even if the loop was never started or already stopped.
    """
    global _reconcile_task

    if _reconcile_task is None or _reconcile_task.done():
        _reconcile_task = None
        return

    _reconcile_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(_reconcile_task), timeout=10.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        _reconcile_task = None
    _logger.info("reconcile_loop_shutdown_complete")
