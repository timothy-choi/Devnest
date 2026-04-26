"""Bounded polling loop for workspace job execution (V1 worker process).

Runs independently of the FastAPI app. Uses the same dequeue semantics as
:func:`~app.workers.workspace_job_worker.worker.run_pending_jobs` (``FOR UPDATE SKIP LOCKED``,
per-job commit).

Usage::

    python -m app.workers.workspace_job_poll_loop

Environment: ``DATABASE_URL`` must point at the application database (same as the API).

Graceful shutdown: SIGINT / SIGTERM set a stop flag; the current poll iteration completes before exit.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from app.libs.common.config import (
    database_host_and_name_for_log,
    format_database_url_for_log,
    get_settings,
    oauth_startup_status_for_log,
)
from app.libs.db.database import get_engine
from app.services.orchestrator_service.app_factory import build_orchestrator_for_workspace_job
from app.services.orchestrator_service.errors import AppOrchestratorBindingError
from app.services.storage.factory import get_snapshot_storage_provider, snapshot_storage_log_fields

from app.services.placement_service.node_heartbeat import try_emit_default_local_execution_node_heartbeat

from .workspace_job_worker.worker import poll_workspace_jobs_tick

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Poll and process QUEUED workspace jobs.")
    p.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        metavar="SEC",
        help="Sleep between ticks when no work was found (default: 2).",
    )
    p.add_argument(
        "--jobs-per-tick",
        type=int,
        default=1,
        metavar="N",
        help="Max jobs to dequeue per wake-up (default: 1).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging level (default: INFO).",
    )
    return p.parse_args(argv)


def run_poll_loop(
    *,
    poll_interval_sec: float = 2.0,
    jobs_per_tick: int = 1,
    stop_event: threading.Event | None = None,
) -> None:
    """
    Wake periodically; each iteration processes up to ``jobs_per_tick`` jobs then sleeps if idle.

    ``stop_event`` when set causes the loop to exit after the current tick. If ``None``, SIGINT /
    SIGTERM install a process-wide stop event.
    """
    ws = get_settings()
    db_host, _db_name = database_host_and_name_for_log(ws.database_url)
    logger.info(
        "[DevNest diagnostics] workspace-worker startup database_host=%s database_target=%s base_domain=%s public_scheme=%s public_port=%s expect_external_postgres=%s",
        db_host,
        format_database_url_for_log(ws.database_url),
        ws.devnest_base_domain,
        ws.devnest_gateway_public_scheme,
        ws.devnest_gateway_public_port,
        ws.devnest_expect_external_postgres,
    )
    oauth_status = oauth_startup_status_for_log(ws)
    logger.info(
        "[DevNest diagnostics] workspace-worker startup frontend_public_base_url=%s github_oauth_public_base_url=%s "
        "gcloud_oauth_public_base_url=%s github_oauth_configured=%s google_oauth_configured=%s",
        oauth_status["frontend_public_base_url"],
        oauth_status["github_oauth_public_base_url"],
        oauth_status["gcloud_oauth_public_base_url"],
        oauth_status["github_oauth_configured"],
        oauth_status["google_oauth_configured"],
    )
    get_snapshot_storage_provider()
    snapshot_fields = snapshot_storage_log_fields()
    logger.info(
        "[DevNest diagnostics] workspace-worker startup snapshot_storage provider=%s bucket=%s prefix=%s region=%s root=%s",
        snapshot_fields.get("provider", ""),
        snapshot_fields.get("bucket", "-"),
        snapshot_fields.get("prefix", "-"),
        snapshot_fields.get("region", "-"),
        snapshot_fields.get("root", "-"),
    )
    if db_host == "postgres" and not ws.devnest_expect_external_postgres:
        logger.info(
            "[DevNest diagnostics] workspace-worker using bundled Postgres host `postgres` "
            "(same DSN resolution as API; override via compose DATABASE_URL for RDS)."
        )
    engine = get_engine()
    own_stop = stop_event is None
    evt = stop_event or threading.Event()

    if own_stop:

        def _handle_signal(_signum: int, _frame: object | None) -> None:
            logger.info("workspace_job_poll_stop_signal")
            evt.set()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

    interval = max(0.1, float(poll_interval_sec))
    limit = max(1, int(jobs_per_tick))

    hb_stop = threading.Event()
    hb_thread: threading.Thread | None = None
    if bool(getattr(ws, "devnest_node_heartbeat_enabled", False)):
        from app.workers.execution_node_heartbeat_emitter import run_execution_node_heartbeat_emitter_loop

        hb_thread = threading.Thread(
            target=run_execution_node_heartbeat_emitter_loop,
            args=(engine, hb_stop),
            name="execution-node-heartbeat",
            daemon=True,
        )
        hb_thread.start()

    logger.info(
        "workspace_job_poll_loop_start",
        extra={"poll_interval_sec": interval, "jobs_per_tick": limit},
    )
    # One immediate heartbeat when the dedicated emitter is off (per-tick path still applies).
    try_emit_default_local_execution_node_heartbeat(engine)

    try:
        while not evt.is_set():
            try:
                tick = poll_workspace_jobs_tick(
                    engine,
                    get_orchestrator=build_orchestrator_for_workspace_job,
                    limit=limit,
                )
            except AppOrchestratorBindingError as e:
                logger.error("workspace_job_poll_orchestrator_bind_failed", extra={"error": str(e)})
                time.sleep(interval)
                continue
            except Exception:
                logger.exception("workspace_job_poll_tick_failed")
                time.sleep(interval)
                continue

            if tick.processed_count > 0:
                logger.info(
                    "workspace_job_poll_tick_done",
                    extra={
                        "processed_count": tick.processed_count,
                        "last_job_id": tick.last_job_id,
                    },
                )
                continue

            # Idle: wait for interval or stop.
            if evt.wait(timeout=interval):
                break
    finally:
        hb_stop.set()
        if hb_thread is not None:
            hb_thread.join(timeout=min(15.0, float(interval) + 10.0))

    logger.info("workspace_job_poll_loop_stop")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        run_poll_loop(
            poll_interval_sec=args.poll_interval,
            jobs_per_tick=args.jobs_per_tick,
        )
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
