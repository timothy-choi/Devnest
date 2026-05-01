"""Background autoscaler loop for the standalone workspace-worker process."""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from app.libs.observability import metrics as devnest_metrics
from app.libs.observability.log_events import log_event
from app.services.autoscaler_service.service import run_scale_out_tick

logger = logging.getLogger(__name__)


def _sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)


def run_autoscaler_loop_tick(engine: Engine) -> tuple[str, str | None]:
    """Run one autoscaler evaluation/provision tick and commit any lifecycle changes."""
    sm = _sessionmaker(engine)
    with sm() as session:
        log_event(logger, "autoscaler.loop.tick")
        try:
            decision, node = run_scale_out_tick(session)
            devnest_metrics.record_autoscaler_decision(
                action=decision.action,
                scale_out_recommended=decision.scale_out_recommended,
            )
            log_event(
                logger,
                "autoscaler.loop.decision",
                action=decision.action,
                scale_out_recommended=decision.scale_out_recommended,
                suppressed_by_config=decision.suppressed_by_config,
                suppressed_by_cap=decision.suppressed_by_cap,
                suppressed_by_cooldown=decision.suppressed_by_cooldown,
                pending_placement_jobs=decision.capacity.pending_placement_jobs,
                recent_placement_failures=decision.capacity.recent_placement_failures,
                free_cpu=decision.capacity.free_cpu,
                free_slots=decision.capacity.free_slots,
                reasons=" | ".join(decision.reasons)[:2000],
            )
            if decision.action == "scale_out_recommended":
                log_event(logger, "autoscaler.scale_out.triggered", reasons=" | ".join(decision.reasons)[:2000])
            if node is not None:
                log_event(
                    logger,
                    "autoscaler.scale_out.provisioned",
                    node_key=node.node_key,
                    instance_id=(node.provider_instance_id or "").strip() or None,
                )
            session.commit()
            return decision.action, node.node_key if node is not None else None
        except Exception:
            devnest_metrics.record_autoscaler_provision(result="error")
            session.rollback()
            logger.exception("autoscaler.loop.tick_failed")
            raise


def run_autoscaler_loop(
    engine: Engine,
    stop_event: threading.Event,
    *,
    interval_seconds: float = 15.0,
) -> None:
    """Poll autoscaler decisions until ``stop_event`` is set."""
    interval = max(1.0, float(interval_seconds))
    logger.info("autoscaler_loop_start", extra={"interval_seconds": interval})
    try:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                run_autoscaler_loop_tick(engine)
            except Exception:
                logger.exception("autoscaler_loop_tick_failed")
            elapsed = time.monotonic() - started
            wait_s = max(0.0, interval - elapsed)
            if stop_event.wait(timeout=wait_s):
                break
    finally:
        logger.info("autoscaler_loop_stop")
