"""Application entrypoint. Run: ``uvicorn app.main:app`` from the ``backend`` directory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.libs.db.database import init_db
from app.libs.observability.middleware import CorrelationIdMiddleware
from app.workers.lifespan_worker import start_background_worker, stop_background_worker
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_background_worker()
    try:
        yield
    finally:
        await stop_background_worker()


app = FastAPI(title="DevNest API", lifespan=lifespan)
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
