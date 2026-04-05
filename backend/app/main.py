"""Application entrypoint. Run: ``uvicorn app.main:app`` from the ``backend`` directory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.libs.db.database import init_db
from app.services.auth_service.api.routers.auth import router as auth_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Auth Service", lifespan=lifespan)
app.include_router(auth_router)
