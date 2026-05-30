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
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)

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
    """Per-test isolation: TRUNCATE all data tables before yield.

    Faster than reapplying schema (~5ms vs ~500ms) and sufficient for
    integration tests that don't mutate schema. UUID primary keys mean
    we don't need RESTART IDENTITY.
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE "
            + ", ".join(f"public.{t}" for t in _DATA_TABLES)
            + " CASCADE"
        )
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
