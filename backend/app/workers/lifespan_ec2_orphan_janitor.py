"""Periodic EC2 orphan cleanup + stale execution-node reconciliation (API process)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

_logger = logging.getLogger(__name__)

_janitor_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]


def _run_ec2_orphan_janitor_tick_sync() -> dict:
    from app.libs.common.config import get_settings  # noqa: PLC0415
    from app.libs.db.database import get_engine  # noqa: PLC0415
    from sqlmodel import Session  # noqa: PLC0415

    from app.services.infrastructure_service.ec2_cleanup import (  # noqa: PLC0415
        cleanup_devnest_autocleanup_orphans,
        reconcile_stale_ec2_execution_nodes,
    )
    from app.services.providers.ec2_provider import build_ec2_client  # noqa: PLC0415

    settings = get_settings()
    engine = get_engine()
    reconciled = 0
    if (settings.aws_region or "").strip():
        with Session(engine) as session:
            reconciled = reconcile_stale_ec2_execution_nodes(session)
            session.commit()
    client = build_ec2_client()
    stats = cleanup_devnest_autocleanup_orphans(client, settings)
    return {"reconciled_nodes": reconciled, **stats}


async def _ec2_orphan_janitor_loop(*, poll_interval: float) -> None:
    loop = asyncio.get_running_loop()
    _logger.info("ec2_orphan_janitor_loop_started", extra={"poll_interval_seconds": poll_interval})
    try:
        while True:
            try:
                summary = await loop.run_in_executor(None, _run_ec2_orphan_janitor_tick_sync)
                _logger.info("ec2_orphan_janitor_tick_complete", extra=summary)
            except Exception:
                _logger.warning("ec2_orphan_janitor_tick_error", exc_info=True)
            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
    except asyncio.CancelledError:
        pass
    finally:
        _logger.info("ec2_orphan_janitor_loop_stopped")


def start_ec2_orphan_janitor_loop() -> Optional[asyncio.Task]:  # type: ignore[type-arg]
    """Start janitor when ``DEVNEST_EC2_ORPHAN_JANITOR_ENABLED`` is true."""
    global _janitor_task

    from app.libs.common.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if not getattr(settings, "devnest_ec2_orphan_janitor_enabled", False):
        _logger.info(
            "ec2_orphan_janitor_disabled",
            extra={"reason": "DEVNEST_EC2_ORPHAN_JANITOR_ENABLED is not true"},
        )
        return None

    poll_interval = float(getattr(settings, "devnest_ec2_orphan_janitor_interval_seconds", 3600))
    poll_interval = max(60.0, poll_interval)

    _janitor_task = asyncio.create_task(
        _ec2_orphan_janitor_loop(poll_interval=poll_interval),
        name="devnest-ec2-orphan-janitor",
    )
    _logger.info(
        "ec2_orphan_janitor_started",
        extra={"poll_interval_seconds": poll_interval},
    )
    return _janitor_task


async def stop_ec2_orphan_janitor_loop() -> None:
    global _janitor_task

    if _janitor_task is None or _janitor_task.done():
        _janitor_task = None
        return

    _janitor_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(_janitor_task), timeout=30.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        _janitor_task = None
    _logger.info("ec2_orphan_janitor_shutdown_complete")
