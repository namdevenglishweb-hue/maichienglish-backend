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
#   - Local dev (no Docker)         → 87 unit tests run, integration SKIPPED
#   - CI integration job (env set)  → all tests run
# Sprint 2 will populate this lane; for now it's infrastructure-only.


def pytest_collection_modifyitems(config, items):
    if os.getenv("MAICHI_TEST_DB"):
        return  # DB available — let everything run
    skip = pytest.mark.skip(
        reason="needs DB (set MAICHI_TEST_DB=1 + a live Postgres to run)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
