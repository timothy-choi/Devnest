"""Application entrypoint. Run: ``uvicorn app.main:app`` from the ``backend`` directory."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.libs.common.config import database_host_and_name_for_log, get_settings
from app.libs.db.database import init_db
from app.libs.events.workspace_event_bus import get_event_bus
from app.libs.observability.middleware import CorrelationIdMiddleware
from app.libs.security.rate_limit import RateLimitMiddleware
from app.workers.lifespan_worker import start_background_worker, stop_background_worker
from app.workers.lifespan_reconcile import start_reconcile_loop, stop_reconcile_loop
from app.libs.observability.routes import router as observability_router
from app.services.audit_service.api.routers import router as audit_router
from app.services.auth_service.api.routers.auth import router as auth_router
from app.services.notification_service.api.routers import internal_notifications_router, notifications_router
from app.services.user_service.api.routers import users_router
from app.services.autoscaler_service.api.routers import internal_autoscaler_router
from app.services.infrastructure_service.api.routers import internal_execution_nodes_router
from app.services.policy_service.api.routers import router as policy_router
from app.services.policy_service.errors import PolicyViolationError
from app.services.quota_service.api.routers import router as quota_router
from app.services.quota_service.errors import QuotaExceededError
from app.services.usage_service.api.routers import router as usage_router
from app.services.workspace_service.api.routers import (
    internal_gateway_auth_router,
    internal_workspace_jobs_router,
    internal_workspace_reconcile_router,
    snapshots_router,
    workspace_snapshots_router,
    workspaces_router,
)
from app.services.integration_service.api.routers import (
    provider_tokens_router,
    workspace_ci_router,
    workspace_repos_router,
    workspace_terminal_router,
)

_lifespan_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    db_host, db_name = database_host_and_name_for_log(settings.database_url)
    _lifespan_logger.info(
        "[DevNest diagnostics] API startup database_host=%s database_name=%s",
        db_host,
        db_name,
    )
    init_db()
    # Attach the event loop to the in-process SSE event bus so worker threads can
    # signal SSE generators via call_soon_threadsafe.
    get_event_bus().attach_event_loop(asyncio.get_event_loop())
    start_background_worker()
    start_reconcile_loop()
    try:
        yield
    finally:
        await stop_reconcile_loop()
        await stop_background_worker()


app = FastAPI(title="DevNest API", lifespan=lifespan)
# Rate limiting middleware (global default: 300 req/min per IP; tighter per-route limits below).
# RateLimitMiddleware respects DEVNEST_RATE_LIMIT_ENABLED; set to false to disable.
app.add_middleware(RateLimitMiddleware, default_calls=300, default_period=60)
app.add_middleware(CorrelationIdMiddleware)


@app.exception_handler(PolicyViolationError)
async def _policy_violation_handler(request: Request, exc: PolicyViolationError) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "detail": str(exc),
            "policy": exc.policy_name,
            "action": exc.action,
        },
    )


@app.exception_handler(QuotaExceededError)
async def _quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": str(exc),
            "quota_field": exc.quota_field,
            "limit": exc.limit,
            "current": exc.current,
        },
    )


app.include_router(observability_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(workspaces_router)
app.include_router(workspace_snapshots_router)
app.include_router(snapshots_router)
app.include_router(notifications_router)
app.include_router(internal_notifications_router)
app.include_router(internal_workspace_jobs_router)
app.include_router(internal_workspace_reconcile_router)
app.include_router(internal_execution_nodes_router)
app.include_router(internal_autoscaler_router)
app.include_router(audit_router)
app.include_router(usage_router)
app.include_router(policy_router)
app.include_router(quota_router)
app.include_router(internal_gateway_auth_router)
# Integration routes (Task 1-5: OAuth, repo import, git sync, CI/CD, terminal)
app.include_router(provider_tokens_router)
app.include_router(workspace_repos_router)
app.include_router(workspace_ci_router)
app.include_router(workspace_terminal_router)
