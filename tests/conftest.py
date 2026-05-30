"""Pytest configuration — set test env vars BEFORE any project import.

`config.settings.get_settings()` is `@lru_cache`-d and reads env at first
call. Several modules call it during import (e.g. `utils.jwt_utils` via
function bodies, `services.adapters.supabase_storage`). We need to plant
sentinel values into the environment before pytest collects + imports
the test modules, otherwise `Settings(...)` raises `ValidationError` on
the required `DATABASE_URL` field.
"""

import os

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
