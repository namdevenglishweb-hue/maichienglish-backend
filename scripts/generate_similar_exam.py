"""Generate a similar exam from a source exam via AI (Mode 1, power-user path).

Usage (from repo root):
    python scripts/generate_similar_exam.py --source <exam_id> --k 3
    python scripts/generate_similar_exam.py --source <id> --k 5 --title "PET 7 (AI)"
    python scripts/generate_similar_exam.py --source <id> --k 3 --dry-run
    python scripts/generate_similar_exam.py --source <id> --k 3 \
        --section-prompts prompts.json     # {"<sectionId>": "idea for this section"}

Connects to DATABASE_URL from .env (dev by default). Requires ANTHROPIC_API_KEY.
Exit codes: 0 ok · 1 generation aborted (all-or-nothing) · 2 bad input.
See docs/exam-ai-generation/exam-ai-generation-design.md §13.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.database import close_db_pool, init_db_pool  # noqa: E402
from services.exam_generation_service import (  # noqa: E402
    GenerationAborted,
    exam_generation_service,
)
from services.exceptions import NotFoundError, ValidationError  # noqa: E402


def _print_report(report: dict) -> None:
    print("=" * 60)
    print(f"  new_exam_id : {report.get('new_exam_id')}"
          f"{'  (DRY RUN — not saved)' if report.get('dry_run') else ''}")
    print(f"  sections    : {report.get('sections_ok')}/{report.get('sections_total')} ok")
    tu = report.get("token_usage") or {}
    print(f"  tokens      : in={tu.get('input')} out={tu.get('output')}")
    sr = report.get("self_review") or {}
    for pos, info in sorted(sr.items()):
        issues = info.get("final_issues") or []
        flag = f" — {len(issues)} residual issue(s)" if issues else ""
        print(f"    §{pos}: self-review {info.get('rounds')} round(s){flag}")
    todos = report.get("media_todos") or []
    if todos:
        print(f"  MEDIA TO REPLACE ({len(todos)}):")
        for t in todos:
            print(f"    §{t['section_position']} material[{t['material_index']}] "
                  f"({t['media_type']}) — replace file, then meta.pendingReplacement=false")
    print("=" * 60)


async def _run(args) -> int:
    section_prompts = None
    if args.section_prompts:
        section_prompts = json.loads(Path(args.section_prompts).read_text(encoding="utf-8"))

    await init_db_pool()
    try:
        report = await exam_generation_service.generate_similar_exam(
            args.source, args.k, created_by=args.created_by, title=args.title,
            section_prompts=section_prompts, dry_run=args.dry_run,
        )
        _print_report(report)
        return 0
    except (NotFoundError, ValidationError) as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2
    except GenerationAborted as e:
        print(f"ABORTED: {e.reason}", file=sys.stderr)
        _print_report(e.report)
        return 1
    finally:
        await close_db_pool()


def main() -> None:
    p = argparse.ArgumentParser(description="AI-generate a similar exam (Mode 1).")
    p.add_argument("--source", required=True, help="Source exam UUID.")
    p.add_argument("--k", required=True, type=int, choices=range(1, 6),
                   help="Variation level 1..5.")
    p.add_argument("--title", default=None, help="New exam title (default '{src} (AI K{k})').")
    p.add_argument("--created-by", dest="created_by", default=None,
                   help="Admin profile UUID to stamp as creator.")
    p.add_argument("--section-prompts", dest="section_prompts", default=None,
                   help="Path to JSON map {sectionId: prompt} (source B).")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Generate + validate + print report, but do NOT save.")
    args = p.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
