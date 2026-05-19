import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from config.database import close_db_pool, get_db_pool, init_db_pool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
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
