"""Background EC2 host disk/memory probe loop (SSM) for the workspace-worker process."""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.placement_service.node_resource_monitor import run_ec2_node_resource_monitor_tick

logger = logging.getLogger(__name__)


def _sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)


def run_node_resource_monitor_loop_tick(engine: Engine) -> int:
    """Run one resource monitor pass; commit updates to ``execution_node``."""
    sm = _sessionmaker(engine)
    with sm() as session:
        try:
            n = run_ec2_node_resource_monitor_tick(session)
            session.commit()
            return n
        except Exception:
            session.rollback()
            raise


def run_node_resource_monitor_loop(
    engine: Engine,
    stop_event: threading.Event,
    *,
    interval_seconds: float | None = None,
) -> None:
    """Probe READY SSM EC2 nodes until ``stop_event`` is set."""
    ws = get_settings()
    if not bool(getattr(ws, "devnest_node_resource_monitor_enabled", True)):
        logger.info("node_resource_monitor_loop_disabled")
        return
    interval = interval_seconds
    if interval is None:
        interval = float(getattr(ws, "devnest_node_resource_check_interval_seconds", 60) or 60)
    interval = max(15.0, float(interval))
    logger.info("node_resource_monitor_loop_start", extra={"interval_seconds": interval})
    try:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                run_node_resource_monitor_loop_tick(engine)
            except Exception:
                logger.exception("node_resource_monitor_loop_tick_failed")
            elapsed = time.monotonic() - started
            wait_s = max(0.0, interval - elapsed)
            if stop_event.wait(timeout=wait_s):
                break
    finally:
        logger.info("node_resource_monitor_loop_stop")
