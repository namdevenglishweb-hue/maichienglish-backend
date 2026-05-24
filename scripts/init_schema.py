"""Apply schema.sql to the database referenced by DATABASE_URL.

Usage (from repo root):
    python scripts/init_schema.py             # apply schema.sql (errors if tables exist)
    python scripts/init_schema.py --drop      # drop existing public tables first (prompts to confirm)
    python scripts/init_schema.py --drop -y   # drop without prompt
    python scripts/init_schema.py --check     # verify connection only
"""
import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import asyncpg  # noqa: E402

from config.settings import get_settings  # noqa: E402

SCHEMA_FILE = PROJECT_ROOT / "schema.sql"

# DROP ... CASCADE handles FK dependencies, but every table must be listed
# so nothing survives a reset (otherwise the next CREATE TABLE collides).
PUBLIC_TABLES = [
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


async def check_connection(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("SELECT version()")
        print(f"OK Connected. {version.split(',')[0]}")
    finally:
        await conn.close()


async def drop_tables(dsn: str) -> None:
    sql = (
        "DROP TABLE IF EXISTS "
        + ", ".join(f"public.{t}" for t in PUBLIC_TABLES)
        + " CASCADE;"
    )
    conn = await asyncpg.connect(dsn)
    try:
        print(f"Dropping: {', '.join(PUBLIC_TABLES)}")
        await conn.execute(sql)
        print("OK Dropped.")
    finally:
        await conn.close()


async def run_schema(dsn: str) -> None:
    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(f"schema.sql not found at {SCHEMA_FILE}")
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    conn = await asyncpg.connect(dsn)
    try:
        print(f"Running {SCHEMA_FILE.name} ...")
        await conn.execute(sql)
        print("OK Schema applied.")
    finally:
        await conn.close()


async def main(args: argparse.Namespace) -> None:
    dsn = get_settings().database_url

    if args.check:
        await check_connection(dsn)
        return

    if args.drop:
        if not args.yes:
            confirm = input(
                f"This will DROP tables: {', '.join(PUBLIC_TABLES)} on the configured DB. "
                "Type 'yes' to confirm: "
            )
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return
        await drop_tables(dsn)

    await run_schema(dsn)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Initialize or reset the Mai Chi English database schema."
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop existing public tables before applying schema (DESTRUCTIVE).",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation when used with --drop.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify DATABASE_URL connection only; don't execute schema.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except Exception as e:
        print(f"ERROR {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
