"""Pytest configuration — set test env vars BEFORE any project import.

`config.settings.get_settings()` is `@lru_cache`-d and reads env at first
call. Several modules call it during import (e.g. `utils.jwt_utils` via
function bodies, `services.adapters.supabase_storage`). We need to plant
sentinel values into the environment before pytest collects + imports
the test modules, otherwise `Settings(...)` raises `ValidationError` on
the required `DATABASE_URL` field.
"""

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://test:test@localhost:5432/test_db",
)
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-not-for-production-use-32-chars",
)
# Storage env stays unset by default so test_storage_adapter can verify
# the missing-env RuntimeError path. Tests that need the adapter to init
# successfully set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY via monkeypatch.


# ---------------------------------------------------------------------------
# Integration test gating
# ---------------------------------------------------------------------------
# Tests marked `@pytest.mark.integration` hit a real Postgres. They are
# auto-skipped unless MAICHI_TEST_DB=1 is set so:
#   - Local dev (no Docker)         → unit tests run, integration SKIPPED
#   - CI integration job (env set)  → all tests run


def pytest_collection_modifyitems(config, items):
    if os.getenv("MAICHI_TEST_DB"):
        return  # DB available — let everything run
    skip = pytest.mark.skip(
        reason="needs DB (set MAICHI_TEST_DB=1 + a live Postgres to run)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------------------
# Integration fixtures (loaded lazily — only used by integration tests).
# Sessions-scoped pool so we don't re-apply schema for every test.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

import asyncpg  # noqa: E402
import pytest_asyncio  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_FILE = _PROJECT_ROOT / "schema.sql"

# Data tables, parent-last for readability. TRUNCATE ... CASCADE cleans
# all of them in one statement irrespective of order.
_DATA_TABLES = [
    "answers",
    "attempt_section_state",
    "attempts",
    "questions",
    "sections",
    "exams",
    "password_reset_codes",
    "subscriptions",
    "profiles",
]


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Session-scoped asyncpg pool with schema applied.

    Connects to `MAICHI_TEST_DATABASE_URL` (or DATABASE_URL fallback),
    drops all data tables, re-applies `schema.sql`, then publishes the
    pool to `config.database._pool` so service-layer code finds it via
    `get_db_pool()`. This lets us skip the app's FastAPI lifespan in
    `client` (no duplicate pool init/close churn).
    """
    dsn = os.environ.get("MAICHI_TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    # max_size=20 because the race-condition tests (Z6 in
    # test_attempt_service_start.py) fire 50 concurrent start_attempt calls.
    # Small pool + many waiters has caused occasional TRUNCATE deadlocks in
    # CI between tests, since lingering session state from gather()'d coroutines
    # can hold AccessShareLock against the next test's TRUNCATE.
    pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=20)

    async with pool.acquire() as conn:
        drop_sql = (
            "DROP TABLE IF EXISTS "
            + ", ".join(f"public.{t}" for t in _DATA_TABLES)
            + " CASCADE;"
        )
        await conn.execute(drop_sql)
        await conn.execute(_SCHEMA_FILE.read_text(encoding="utf-8"))

    from config import database

    database._pool = pool
    try:
        yield pool
    finally:
        database._pool = None
        await pool.close()


@pytest_asyncio.fixture
async def db(db_pool):
    """Per-test isolation via DELETE on a separate (non-pool) connection.

    TRUNCATE was deadlocking in CI: pool connections from the previous
    test sometimes still hold AccessShareLock on data tables when the
    next test's TRUNCATE asks for AccessExclusiveLock on the same set,
    while we (the TRUNCATE caller) hold AccessShareLock on another set
    via FK validation. Postgres detects the cycle and aborts us.

    Two changes to remove the failure surface:
      1. DELETE instead of TRUNCATE — only takes RowExclusiveLock,
         doesn't conflict with the pool's idle AccessShareLocks.
      2. Run on a dedicated `asyncpg.connect()` outside the app pool,
         so we don't share catalog/prepared-statement state with any
         coroutine still in flight from the previous test.

    Iterating `_DATA_TABLES` in declared (children → parents) order
    makes ON DELETE CASCADE happy without explicit ordering. ~10ms.
    """
    dsn = os.environ.get("MAICHI_TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    cleanup_conn = await asyncpg.connect(dsn)
    try:
        for table in _DATA_TABLES:
            await cleanup_conn.execute(f"DELETE FROM public.{table}")
    finally:
        await cleanup_conn.close()
    yield db_pool


@pytest_asyncio.fixture
async def client(db):
    """httpx.AsyncClient bound to the FastAPI app via ASGI transport.

    Drives the lifespan via `asgi-lifespan` so any startup hooks run.
    `init_db_pool()` is a no-op because `db_pool` already set `_pool`.
    Importing `main` inside the fixture defers app construction until
    after env vars + the pool are in place.
    """
    from main import app

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------------------
# Helper fixtures — used by integration tests to seed data + auth.
# Kept here (not a separate module) so they're auto-discoverable.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def make_user(db):
    """Factory that creates a user via `user_service.create_user`.

    Returns the created user dict augmented with the plaintext
    `password` (so tests can login as that user). Goes through the real
    service path → exercises bcrypt + subscription txn.
    """
    from services.user_service import user_service

    created: list[str] = []  # ids — kept for debug; TRUNCATE handles cleanup

    async def _make(
        email: str = "user@maichienglish.test",
        password: str = "TestPassword123",
        full_name: str = "Test User",
        role: str = "student",
        phone=None,
        tier: str = "free",
        parent_id=None,
    ) -> dict:
        user = await user_service.create_user(
            email=email,
            password=password,
            full_name=full_name,
            role=role,
            phone=phone,
            tier=tier,
            parent_id=parent_id,
        )
        created.append(user["id"])
        user["password"] = password  # echo plaintext for login flows
        return user

    return _make


@pytest.fixture
def auth_headers():
    """Factory fixture — `auth_headers(email, role=..., tier=...)` returns
    a Bearer header dict signed by the same JWT secret the app uses.

    The user must already exist in the DB for endpoints that look it up
    via `get_by_email` (e.g. /api/users/me, /api/auth/refresh). For
    endpoints that only check role/tier from the token (the
    require_admin family), the DB row isn't queried.
    """
    from utils.jwt_utils import create_access_token

    def _make(email: str, role: str = "student", tier: str = "free") -> dict:
        token = create_access_token(email=email, role=role, tier=tier)
        return {"Authorization": f"Bearer {token}"}

    return _make


# ---------------------------------------------------------------------------
# Exam / Section / Question / Attempt factories — raw SQL (fast).
#
# Service-layer (`exam_service.create_exam_nested`) goes through Pydantic
# validation, transaction wrapping, gap-marker validation, etc. — too much
# for test setup. Raw SQL inserts give predictable seed data in ~5ms.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def make_exam(db_pool):
    """Factory: creates exam (+optional sections + questions). Returns dict
    with `id`, `sections` (each carrying `id` + `questions`).

    Defaults: published=True, level="KET", skill="listening". Pass
    `published=False` to test draft-mode preconditions (E1).

    `sections` arg is a list of:
      [{"type": "multiple_choice", "questions": [
          {"question_type": "multiple_choice", "question_data": {...}},
          ...
      ]}, ...]
    """
    import json
    import uuid

    async def _make(
        title: str = "Test KET Listening",
        level: str = "KET",
        skill: str = "listening",
        published: bool = True,
        deleted: bool = False,
        sections: list | None = None,
    ) -> dict:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.exams
                        (title, level, skill, is_published, deleted_at)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                    """,
                    title,
                    level,
                    skill,
                    published,
                    None if not deleted else "now()",
                )
                exam_id = str(row["id"])

                created_sections = []
                for s_pos, s in enumerate(sections or [], start=1):
                    sec_row = await conn.fetchrow(
                        """
                        INSERT INTO public.sections
                            (exam_id, position, type, materials)
                        VALUES ($1, $2, $3, $4::jsonb)
                        RETURNING id
                        """,
                        uuid.UUID(exam_id),
                        s_pos,
                        s.get("type", "multiple_choice"),
                        json.dumps(s.get("materials", [])),
                    )
                    sec_id = str(sec_row["id"])

                    created_questions = []
                    for q_pos, q in enumerate(s.get("questions", []), start=1):
                        q_row = await conn.fetchrow(
                            """
                            INSERT INTO public.questions
                                (section_id, position, question_type,
                                 question_data, points)
                            VALUES ($1, $2, $3, $4::jsonb, $5)
                            RETURNING id
                            """,
                            uuid.UUID(sec_id),
                            q_pos,
                            q["question_type"],
                            json.dumps(q["question_data"]),
                            q.get("points", 1),
                        )
                        created_questions.append({
                            "id": str(q_row["id"]),
                            "question_type": q["question_type"],
                            "question_data": q["question_data"],
                            "points": q.get("points", 1),
                        })

                    created_sections.append({
                        "id": sec_id,
                        "type": s.get("type", "multiple_choice"),
                        "questions": created_questions,
                    })

        return {
            "id": exam_id,
            "title": title,
            "level": level,
            "skill": skill,
            "is_published": published,
            "sections": created_sections,
        }

    return _make


@pytest_asyncio.fixture
async def make_attempt(db_pool):
    """Factory: insert an attempt row directly with a chosen state.

    `state`: "in_progress" (default), "submitted", "abandoned".
    Use this to seed quota tests (Q1-Q11) without going through the
    service's INSERT path (which would enforce the partial unique
    index — only 1 active per user — making "5 prior + 1 active" impossible
    to seed via the service).

    `started_at` defaults to now(); pass a `datetime` to seed
    period-boundary tests (Q10).
    """
    import uuid
    from datetime import datetime, timezone

    async def _make(
        user_id: str,
        exam_id: str,
        state: str = "in_progress",
        started_at: datetime | None = None,
    ) -> dict:
        if state == "in_progress":
            submitted_at = None
            is_abandoned = False
        elif state == "submitted":
            submitted_at = datetime.now(timezone.utc)
            is_abandoned = False
        elif state == "abandoned":
            submitted_at = datetime.now(timezone.utc)
            is_abandoned = True
        else:
            raise ValueError(f"Unknown attempt state: {state}")

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.attempts
                    (user_id, exam_id, started_at, submitted_at, is_abandoned,
                     score, total_points, percentage)
                VALUES ($1, $2, COALESCE($3, now()), $4, $5, $6, $7, $8)
                RETURNING id, started_at, submitted_at
                """,
                uuid.UUID(user_id),
                uuid.UUID(exam_id),
                started_at,
                submitted_at,
                is_abandoned,
                0 if state != "in_progress" else None,
                0 if state != "in_progress" else None,
                0 if state != "in_progress" else None,
            )

        return {
            "id": str(row["id"]),
            "user_id": user_id,
            "exam_id": exam_id,
            "state": state,
            "started_at": row["started_at"],
            "submitted_at": row["submitted_at"],
        }

    return _make


@pytest_asyncio.fixture
async def set_subscription(db_pool):
    """Update an existing subscription row — tier, status, period_start.

    `make_user` already creates a sub with defaults
    (`tier='free', status='active', current_period_start=now()`). This
    fixture lets tests tweak fields after the fact for tier-quota /
    canceled-status / period-boundary scenarios.
    """
    import uuid
    from datetime import datetime

    async def _set(
        user_id: str,
        *,
        tier: str | None = None,
        status: str | None = None,
        current_period_start: datetime | None = None,
    ) -> None:
        sets = []
        vals: list = []
        if tier is not None:
            sets.append(f"tier = ${len(vals) + 1}")
            vals.append(tier)
        if status is not None:
            sets.append(f"status = ${len(vals) + 1}")
            vals.append(status)
        if current_period_start is not None:
            sets.append(f"current_period_start = ${len(vals) + 1}")
            vals.append(current_period_start)
        if not sets:
            return
        vals.append(uuid.UUID(user_id))
        sql = (
            "UPDATE public.subscriptions SET "
            + ", ".join(sets)
            + f", updated_at = now() WHERE user_id = ${len(vals)}"
        )
        async with db_pool.acquire() as conn:
            await conn.execute(sql, *vals)

    return _set


@pytest.fixture
def sample_mc_question_data():
    """Sample multiple_choice question_data — minimal valid shape.

    Use when test just needs A question; the answer is `correct_index=0`
    so `studentAnswer=0` grades correct, anything else grades wrong.
    """
    return {
        "stem": "What is 2+2?",
        "options": [{"text": "4"}, {"text": "5"}, {"text": "6"}],
        "correct_index": 0,
    }
