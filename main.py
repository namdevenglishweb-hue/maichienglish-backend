import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from api.admin import router as admin_router
from api.auth import router as auth_router
from api.subscriptions import router as subscriptions_router
from api.users import router as users_router
from config.database import close_db_pool, get_db_pool, init_db_pool
from config.logging import setup_logging
from config.settings import get_settings

setup_logging(level="DEBUG" if get_settings().debug else "INFO")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_pool()
    yield
    await close_db_pool()


app = FastAPI(
    title="Mai Chi English API",
    version="0.1.0",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins_list,
    allow_origin_regex=_settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(subscriptions_router)
app.include_router(admin_router)


@app.get("/health")
async def health_check():
    """Liveness probe — returns OK as long as the app process is up."""
    return {"status": "ok", "service": "maichienglish-api"}


@app.get("/db-ping")
async def db_ping():
    """Readiness probe — verifies database connectivity with SELECT 1."""
    try:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("DB ping failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unreachable: {e}",
        )
