"""A/B matrix runner for AI exam generation — {promptVersion x model x K}.

Read-only on sources (Mode 2 single part, nothing saved). For every combo it
calls `generate_one_part` and records: status, self-review rounds/issues,
the shadow verbatim-overlap metric (1.0 = clone of the source), token usage
and duration. Full per-run JSON goes to --out; a summary table prints at the
end. Uses the dev DB (DATABASE_URL) + real provider keys from .env.

    python scripts/ab_matrix.py --section <uuid> [--section <uuid> ...]
        [--ks 1,3,5] [--versions v1,v2]
        [--combos openrouter:anthropic/claude-sonnet-4.5,groq:llama-3.3-70b-versatile]
        [--rounds 2] [--out scripts/ab_results]

Cost guard: prints the run count and waits for ENTER unless --yes.
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_COMBOS = "openrouter:anthropic/claude-sonnet-4.5,groq:llama-3.3-70b-versatile"


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--section", action="append", required=True,
                   help="Source section id (repeatable).")
    p.add_argument("--ks", default="1,3,5")
    p.add_argument("--versions", default="v1,v2")
    p.add_argument("--combos", default=DEFAULT_COMBOS,
                   help="Comma list of provider:model pairs.")
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--out", default=os.path.join("scripts", "ab_results"))
    p.add_argument("--yes", action="store_true", help="Skip the cost prompt.")
    return p.parse_args()


async def _run_one(svc, section_id, k, provider, model, version, rounds):
    t0 = time.monotonic()
    try:
        result = await svc.generate_one_part(
            section_id, k, provider=provider, model=model,
            prompt_version=version, rounds=rounds,
        )
        sec = result["sections"][0]
        return {
            "status": sec["status"],
            "self_review": sec.get("self_review"),
            "verbatim_overlap": sec.get("verbatim_overlap"),
            "token_usage": result.get("token_usage"),
            "seconds": round(time.monotonic() - t0, 1),
            "section_payload": sec.get("section"),
        }
    except Exception as e:  # noqa: BLE001 — one failed run must not kill the matrix
        return {
            "status": "error", "error": f"{type(e).__name__}: {e}",
            "seconds": round(time.monotonic() - t0, 1),
        }


async def main() -> None:
    args = _parse_args()
    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    combos = []
    for c in args.combos.split(","):
        provider, _, model = c.strip().partition(":")
        combos.append((provider, model or None))

    runs = [(s, k, pr, mo, v)
            for s in args.section for pr, mo in combos for k in ks for v in versions]
    print(f"Matrix: {len(args.section)} section(s) x {len(combos)} model(s) x "
          f"{len(ks)} K x {len(versions)} version(s) = {len(runs)} runs "
          f"(rounds={args.rounds})")
    if not args.yes:
        input("ENTER to start (Ctrl+C to abort)... ")

    os.makedirs(args.out, exist_ok=True)
    from config.database import init_db_pool, close_db_pool
    await init_db_pool()
    rows = []
    try:
        from services.exam_generation_service import exam_generation_service as svc
        for i, (sid, k, provider, model, version) in enumerate(runs, 1):
            tag = f"{sid[:8]}_{(model or provider).replace('/', '-')}_k{k}_{version}"
            print(f"[{i}/{len(runs)}] {tag} ({model}) ...", flush=True)
            r = await _run_one(svc, sid, k, provider, model, version, args.rounds)
            r.update({"section_id": sid, "k": k, "provider": provider,
                      "model": model, "prompt_version": version,
                      "rounds": args.rounds})
            with open(os.path.join(args.out, tag + ".json"), "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=2)
            rows.append(r)
    finally:
        await close_db_pool()

    print("\n=== SUMMARY (overlap: 1.0 = clone of source) ===")
    hdr = f"{'section':<10}{'model':<14}{'K':<3}{'ver':<5}{'status':<8}" \
          f"{'ov.max':<8}{'ov.avg':<8}{'sr':<4}{'crit':<5}{'tokens in/out':<16}{'sec':<6}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ov = r.get("verbatim_overlap") or {}
        sr = r.get("self_review") or {}
        crit = sum(1 for i in (sr.get("final_issues") or [])
                   if i.get("severity") == "critical")
        tu = r.get("token_usage") or {}
        print(f"{r['section_id'][:8]:<10}{(r['model'] or '')[:13]:<14}{r['k']:<3}"
              f"{r['prompt_version']:<5}{r['status']:<8}"
              f"{ov.get('max', '-'):<8}{ov.get('weighted_avg', '-'):<8}"
              f"{sr.get('rounds', '-'):<4}{crit:<5}"
              f"{str(tu.get('input', '-')) + '/' + str(tu.get('output', '-')):<16}"
              f"{r['seconds']:<6}")
    print(f"\nFull outputs in {args.out}/")


if __name__ == "__main__":
    asyncio.run(main())
