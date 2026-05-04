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
from app.services.autoscaler_service.service import (
    execute_scale_down,
    reclaim_one_idle_ec2_node,
    run_scale_out_tick,
)

logger = logging.getLogger(__name__)

_SCALE_DOWN_INTERVAL_SECONDS = 60.0


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
                scale_in_recommended=decision.scale_in_recommended,
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
            reclaimed_node = None
            if decision.action == "scale_in_recommended":
                log_event(logger, "autoscaler.scale_down.triggered", reasons=" | ".join(decision.reasons)[:2000])
                reclaimed_node = execute_scale_down(session, decision)
            if node is not None:
                log_event(
                    logger,
                    "autoscaler.scale_out.provisioned",
                    node_key=node.node_key,
                    instance_id=(node.provider_instance_id or "").strip() or None,
                )
            session.commit()
            changed_node = node or reclaimed_node
            return decision.action, changed_node.node_key if changed_node is not None else None
        except Exception:
            devnest_metrics.record_autoscaler_provision(result="error")
            session.rollback()
            logger.exception("autoscaler.loop.tick_failed")
            raise


def run_autoscaler_scale_down_tick(engine: Engine) -> str | None:
    """Run one automatic scale-down tick and commit any node lifecycle changes."""
    sm = _sessionmaker(engine)
    with sm() as session:
        log_event(logger, "autoscaler.scale_down.tick")
        try:
            node = reclaim_one_idle_ec2_node(session)
            session.commit()
            return node.node_key if node is not None else None
        except Exception:
            session.rollback()
            logger.exception("autoscaler.scale_down.tick_failed")
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
    last_scale_down_at = 0.0
    try:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                action, _node_key = run_autoscaler_loop_tick(engine)
                now = time.monotonic()
                if action == "scale_in_recommended":
                    last_scale_down_at = now
                elif now - last_scale_down_at >= _SCALE_DOWN_INTERVAL_SECONDS:
                    run_autoscaler_scale_down_tick(engine)
                    last_scale_down_at = now
            except Exception:
                logger.exception("autoscaler_loop_tick_failed")
            elapsed = time.monotonic() - started
            wait_s = max(0.0, interval - elapsed)
            if stop_event.wait(timeout=wait_s):
                break
    finally:
        logger.info("autoscaler_loop_stop")
