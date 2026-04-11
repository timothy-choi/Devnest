"""Application entrypoint. Run: ``uvicorn app.main:app`` from the ``backend`` directory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.libs.db.database import init_db
from app.services.auth_service.api.routers.auth import router as auth_router
from app.services.notification_service.api.routers import internal_notifications_router, notifications_router
from app.services.user_service.api.routers import users_router
from app.services.workspace_service.api.routers import (
    internal_workspace_jobs_router,
    workspaces_router,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="DevNest API", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(workspaces_router)
app.include_router(notifications_router)
app.include_router(internal_notifications_router)
app.include_router(internal_workspace_jobs_router)
