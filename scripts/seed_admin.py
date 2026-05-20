"""Seed an initial admin user from environment variables.

Reads ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME from env / .env.
Idempotent: if a user with the given email already exists, prints a notice
and exits 0.

Usage (from repo root, with venv activated):
    python scripts/seed_admin.py
"""
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Minimal .env loader so ADMIN_* are accessible without installing python-dotenv.
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")

from config.database import close_db_pool, init_db_pool  # noqa: E402
from services.exceptions import AlreadyExistsError  # noqa: E402
from services.user_service import user_service  # noqa: E402


async def main() -> int:
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    full_name = os.environ.get("ADMIN_FULL_NAME")

    if not (email and password and full_name):
        print(
            "ERROR: Set ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME in env or .env",
            file=sys.stderr,
        )
        return 1

    await init_db_pool()
    try:
        try:
            user = await user_service.create_user(
                email=email,
                password=password,
                full_name=full_name,
                role="admin",
                tier="ultra",
            )
            print(f"OK Admin created: id={user['id']} email={user['email']}")
        except AlreadyExistsError as e:
            print(f"INFO {e}")
    finally:
        await close_db_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
