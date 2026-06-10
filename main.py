import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.admin import router as admin_router
from api.ai_settings import admin_router as ai_settings_router
from api.attempts import router as attempts_router
from api.auth import router as auth_router
from api.classes import admin_router as admin_classes_router
from api.classes import me_router as student_classes_router
from api.classes import teacher_router as teacher_classes_router
from api.exam_generations import admin_router as exam_generations_router
from api.exams import router as exams_router
from api.image_generations import admin_router as image_generations_router
from api.parents import router as parents_router
from api.section_type_prompts import admin_router as section_type_prompts_router
from api.questions import question_router as questions_router
from api.questions import section_scoped_router as section_questions_router
from api.sections import exam_scoped_router as exam_sections_router
from api.sections import section_router as sections_router
from api.subscriptions import router as subscriptions_router
from api.teacher import router as teacher_router
from api.users import router as users_router
from config.database import close_db_pool, get_db_pool, init_db_pool
from config.logging import setup_logging
from config.settings import get_settings
from services.exceptions import ConflictError

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


@app.exception_handler(ConflictError)
async def _conflict_handler(request: Request, exc: ConflictError):
    """Map any uncaught ConflictError → 409 (e.g. publish-lock guards in
    services/exam_guards.py). Routes that already catch ConflictError
    locally (e.g. attempts) are unaffected — their handler runs first."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
    )

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(subscriptions_router)
app.include_router(admin_router)
app.include_router(exams_router)
app.include_router(exam_sections_router)
app.include_router(sections_router)
app.include_router(section_questions_router)
app.include_router(questions_router)
app.include_router(attempts_router)
app.include_router(parents_router)
app.include_router(teacher_router)
app.include_router(admin_classes_router)
app.include_router(teacher_classes_router)
app.include_router(student_classes_router)
app.include_router(exam_generations_router)
app.include_router(section_type_prompts_router)
app.include_router(image_generations_router)
app.include_router(ai_settings_router)


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
