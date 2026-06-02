"""Apply one or more migration SQL files to the DATABASE_URL Postgres.

Usage (from repo root):
    python scripts/apply_migration.py 0011                    # apply migrations/0011_*.sql
    python scripts/apply_migration.py 0011 0012               # apply both, in order
    python scripts/apply_migration.py migrations/0011_*.sql   # explicit path also works
    python scripts/apply_migration.py --all                   # apply every migrations/*.sql that exists
                                                              #   (idempotent — safe to re-run)
    python scripts/apply_migration.py --sql "ALTER TABLE..."  # inline SQL (useful for one-off
                                                              #   bucket RLS policies that aren't
                                                              #   under migrations/)

Each file is executed in a single transaction. If any statement fails,
the entire file is rolled back.

Migrations in this repo are written to be idempotent (`IF NOT EXISTS`
+ dynamic constraint drops), so applying the same file twice is safe.
"""
import argparse
import asyncio
import glob
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import asyncpg  # noqa: E402

from config.settings import get_settings  # noqa: E402


def _resolve_files(args: list[str]) -> list[Path]:
    """Convert user-supplied tokens (numbers, globs, paths) to migration paths."""
    files: list[Path] = []
    mig_dir = PROJECT_ROOT / "migrations"
    for token in args:
        # Numeric prefix like "0011" → look up under migrations/
        if token.isdigit() or (len(token) == 4 and token.isdigit()):
            matches = sorted(mig_dir.glob(f"{token}_*.sql"))
            if not matches:
                raise FileNotFoundError(f"No migration matching '{token}_*.sql'")
            files.extend(matches)
            continue
        # Glob pattern
        if "*" in token or "?" in token:
            matches = sorted(Path(m) for m in glob.glob(token))
            if not matches:
                raise FileNotFoundError(f"Glob '{token}' matched nothing")
            files.extend(matches)
            continue
        # Explicit path
        p = Path(token)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if not p.exists():
            raise FileNotFoundError(f"Migration file not found: {p}")
        files.append(p)
    return files


async def _apply_sql(conn, label: str, sql: str) -> None:
    print(f"-> Applying {label} ({len(sql)} chars)...")
    async with conn.transaction():
        await conn.execute(sql)
    print(f"   OK {label} applied successfully.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "tokens", nargs="*",
        help="Migration numbers (e.g. 0011), paths, or globs.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Apply every file under migrations/ in numeric order (idempotent).",
    )
    parser.add_argument(
        "--sql", type=str, default=None,
        help="Inline SQL string to execute (for one-off policy / fix snippets).",
    )
    args = parser.parse_args()

    if not args.tokens and not args.all and not args.sql:
        parser.error("Provide migration tokens, --all, or --sql.")

    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        if args.all:
            files = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
            print(f"Found {len(files)} migration(s).")
            for f in files:
                sql = f.read_text(encoding="utf-8")
                await _apply_sql(conn, f.name, sql)
        elif args.tokens:
            files = _resolve_files(args.tokens)
            for f in files:
                sql = f.read_text(encoding="utf-8")
                await _apply_sql(conn, f.name, sql)
        if args.sql:
            await _apply_sql(conn, "inline SQL", args.sql)
    finally:
        await conn.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
