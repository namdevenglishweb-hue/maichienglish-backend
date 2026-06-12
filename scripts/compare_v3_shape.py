"""Prove v3 spec-mode output is structurally identical to v2 output.

Runs generate_one_part on the SAME source section with promptVersion v2 and
v3 (real AI), dumps both generated sections, and diffs their STRUCTURE
recursively: key sets, nesting, value types, plus invariant checks
(correct_index in range, material count/type, question_type/points).

    python scripts/compare_v3_shape.py <section_id> [k]
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROVIDER, MODEL = "openrouter", "anthropic/claude-sonnet-4.5"


def shape_of(value, path="$"):
    """Recursive structural signature: {path: type} (list items merged)."""
    out = {}
    if isinstance(value, dict):
        out[path] = "object"
        for key in sorted(value):
            out.update(shape_of(value[key], f"{path}.{key}"))
    elif isinstance(value, list):
        out[path] = "array"
        for item in value:  # merge all items — catches heterogeneous lists
            out.update(shape_of(item, f"{path}[]"))
    else:
        out[path] = type(value).__name__
    return out


def check_invariants(tag, section):
    errs = []
    for i, m in enumerate(section.get("materials") or []):
        if m.get("type") == "text" and not isinstance(m.get("content"), str):
            errs.append(f"materials[{i}].content not str")
    for i, q in enumerate(section.get("questions") or []):
        qd = q.get("question_data")
        if not isinstance(qd, dict):
            errs.append(f"questions[{i}] missing question_data dict")
            continue
        opts = qd.get("options")
        ci = qd.get("correct_index")
        if not isinstance(opts, list) or not all(
                isinstance(o, dict) and isinstance(o.get("text"), str) for o in opts):
            errs.append(f"questions[{i}].options not [{{text:str}}]")
        if not isinstance(ci, int) or not (0 <= ci < len(opts or [])):
            errs.append(f"questions[{i}].correct_index invalid: {ci!r}")
        if q.get("question_type") != "multiple_choice":
            errs.append(f"questions[{i}].question_type = {q.get('question_type')!r}")
        if not isinstance(q.get("points"), int):
            errs.append(f"questions[{i}].points not int")
    print(f"  [{tag}] invariants: {'OK' if not errs else errs}")
    return errs


async def main():
    section_id = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    from config.database import init_db_pool, close_db_pool
    await init_db_pool()
    try:
        from services.exam_generation_service import exam_generation_service as svc
        from services.ai import spec_mode

        section, ctx = await svc.load_section_for_gen(section_id)
        core = spec_mode.assign_core(section, k, ctx.get("level"))
        print(f"source: level={ctx.get('level')} | questions={len(section['questions'])} "
              f"| materials={len(section['materials'])} | assign_core(k={k}) -> {core}")
        if core is None:
            print("!! section not spec-eligible — pick another section"); return

        results = {}
        for ver in ("v2", "v3"):
            print(f"\n=== running {ver} ===", flush=True)
            r = await svc.generate_one_part(
                section_id, k, provider=PROVIDER, model=MODEL,
                prompt_version=ver, rounds=1)
            entry = r["sections"][0]
            results[ver] = entry
            out = os.path.join("scripts", "ab_results", f"shape_{ver}.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
            print(f"  status={entry['status']} mode={entry.get('mode', '(n/a)')} "
                  f"tokens={r['token_usage']}")

        s2, s3 = results["v2"]["section"], results["v3"]["section"]
        sh2, sh3 = shape_of(s2), shape_of(s3)
        only2 = sorted(set(sh2) - set(sh3))
        only3 = sorted(set(sh3) - set(sh2))
        typediff = sorted(p for p in set(sh2) & set(sh3) if sh2[p] != sh3[p])

        print("\n=== STRUCTURAL DIFF (v2 vs v3) ===")
        print(f"  paths only in v2 : {only2 or 'NONE'}")
        print(f"  paths only in v3 : {only3 or 'NONE'}")
        print(f"  type mismatches  : "
              f"{[f'{p}: v2={sh2[p]} v3={sh3[p]}' for p in typediff] or 'NONE'}")
        e2 = check_invariants("v2", s2)
        e3 = check_invariants("v3", s3)
        identical = not (only2 or only3 or typediff or e2 or e3)
        print(f"\nVERDICT: {'STRUCTURALLY IDENTICAL' if identical else 'SHAPE DIVERGENCE — see above'}")
    finally:
        await close_db_pool()


asyncio.run(main())
