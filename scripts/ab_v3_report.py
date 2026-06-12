"""A/B v2-vs-v3 with FULL human-readable input/output logs.

For every run, writes scripts/ab_results/v3_report/<tag>.md containing:
source section (material/questions/answers), run params (version/model/K/
core/topic/seed), EVERY AI call's actual prompts (system + user — rebuilt
with the exact registry renderers the adapters use) and raw response, the
generated section, and metrics (mode, trigram%, verbatim overlap, verify
verdicts, retry reasons incl. F2 fixed-section merge failures, calls,
tokens, seconds). Plus SUMMARY.md comparing v2 vs v3 side by side.

No production code touched: a recording wrapper delegates to the real
adapter. Prompts/responses never contain API keys.

    python scripts/ab_v3_report.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROVIDER, MODEL = "openrouter", "anthropic/claude-sonnet-4.5"
SECTIONS = ["457eb7cd-7154-4341-9cd9-4bee554c3c8c",   # KET Part 4 MC 5q
            "f11fbad4-8064-484d-b951-f3a309ee7ead"]   # PET Part 5 MC 10q
KS = [3, 5]
VERSIONS = ["v2", "v3"]
ROUNDS = 2
OUT = os.path.join("scripts", "ab_results", "v3_report")


def _j(x):
    return json.dumps(x, ensure_ascii=False, indent=2)


class RecordingGen:
    """Delegates to the real adapter; records prompts (rebuilt via the SAME
    registry renderers the adapters use) + raw responses + timings."""

    def __init__(self, inner):
        self._inner = inner
        self.model = inner.model
        self.provider = inner.provider
        self.calls: list[dict] = []

    @property
    def usage(self):
        return self._inner.usage

    def _pv(self, payload):
        from services.ai import prompts
        return prompts.get_prompt_version(payload.get("prompt_version"))

    async def _record(self, kind, system, user, coro):
        t0 = time.monotonic()
        entry = {"kind": kind, "system": system, "user": user}
        try:
            resp = await coro
            entry["response"] = resp
            return resp
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - t0, 1)
            self.calls.append(entry)

    async def analyze_section(self, payload):
        pv = self._pv(payload)
        return await self._record(
            "ANALYZE", pv.system_analyze, pv.render_analyze(payload),
            self._inner.analyze_section(payload))

    async def generate_section(self, payload, *, k):
        pv = self._pv(payload)
        return await self._record(
            "GENERATE", pv.system_generate, pv.render_generate(payload, k),
            self._inner.generate_section(payload, k=k))

    async def verify_section(self, section, payload, *, k):
        pv = self._pv(payload)
        return await self._record(
            "VERIFY", pv.system_verify, pv.render_verify(section, payload, k),
            self._inner.verify_section(section, payload, k=k))


def _section_md(section):
    lines = [f"- **part_label:** {section.get('part_label')}",
             f"- **instructions:** {section.get('instructions')}"]
    for i, m in enumerate(section.get("materials") or []):
        lines.append(f"\n**Material {i} ({m.get('type')}):**\n\n> "
                     + str(m.get("content") or m.get("url") or "")
                     .replace("\n", "\n> "))
    for q in section.get("questions") or []:
        qd = q.get("question_data") or {}
        lines.append(f"\n**Q{q.get('position')}** ({q.get('question_type')}, "
                     f"{q.get('points')}đ): {qd.get('stem')}")
        for j, o in enumerate(qd.get("options") or []):
            mark = " ✅" if j == qd.get("correct_index") else ""
            lines.append(f"  - [{j}] {o.get('text') if isinstance(o, dict) else o}{mark}")
    return "\n".join(lines)


def _calls_md(calls):
    out = []
    for i, c in enumerate(calls, 1):
        out.append(f"\n### AI call {i} — {c['kind']} ({c['seconds']}s)\n")
        out.append(f"<details><summary>SYSTEM prompt</summary>\n\n```\n{c['system']}\n```\n</details>\n")
        out.append(f"<details><summary>USER prompt</summary>\n\n```\n{c['user']}\n```\n</details>\n")
        if "response" in c:
            out.append(f"<details><summary>RAW response</summary>\n\n```json\n{_j(c['response'])}\n```\n</details>")
        else:
            out.append(f"**ERROR:** `{c.get('error')}`")
    return "\n".join(out)


def _retry_reasons(calls):
    """retry_error values that appeared in GENERATE prompts = why each retry
    happened (incl. F2: fixed_section strict-merge failures)."""
    reasons = []
    for c in calls:
        if c["kind"] == "GENERATE" and "YOUR PREVIOUS ATTEMPT WAS REJECTED" in c["user"]:
            first_line = c["user"].split("\n", 1)[0]
            reasons.append(first_line.replace("YOUR PREVIOUS ATTEMPT WAS REJECTED: ", ""))
    return reasons


async def run_one(svc, sid, k, ver, source, rows):
    from services.ai.generator import get_ai_generator
    rec = RecordingGen(get_ai_generator(provider=PROVIDER, model=MODEL))
    t0 = time.monotonic()
    entry = None
    error = None
    try:
        r = await svc.generate_one_part(sid, k, generator=rec,
                                        prompt_version=ver, rounds=ROUNDS)
        entry = r["sections"][0]
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    secs = round(time.monotonic() - t0, 1)

    tag = f"{sid[:8]}_k{k}_{ver}"
    n_verify = sum(1 for c in rec.calls if c["kind"] == "VERIFY")
    verdicts = [c.get("response", {}).get("is_acceptable")
                for c in rec.calls if c["kind"] == "VERIFY"]
    fixed_used = sum(1 for c in rec.calls if c["kind"] == "VERIFY"
                     and isinstance(c.get("response", {}).get("fixed_section"), dict))
    retries = _retry_reasons(rec.calls)
    f2_count = sum(1 for r_ in retries if "no source fallback" in r_)

    md = [f"# Run {tag} — {MODEL}\n",
          f"- version: **{ver}** | K: **{k}** | rounds: {ROUNDS}",
          f"- status: **{'OK' if entry else 'FAILED'}**"
          + (f" — `{error}`" if error else ""),
          f"- mode: **{(entry or {}).get('mode', 'rewrite (v2)' if ver == 'v2' else '-')}**"
          f" | core: {(entry or {}).get('core', '-')}",
          f"- topic: {(entry or {}).get('topic', '-')}",
          f"- diversity_seed: `{_j((entry or {}).get('diversity_seed')) if entry and entry.get('diversity_seed') else '-'}`",
          f"- trigram_overlap_pct: {(entry or {}).get('trigram_overlap_pct', '-')}"
          f" | verbatim max/avg: {((entry or {}).get('verbatim_overlap') or {}).get('max', '-')}"
          f"/{((entry or {}).get('verbatim_overlap') or {}).get('weighted_avg', '-')}",
          f"- AI calls: {len(rec.calls)} (verify={n_verify}, verdicts={verdicts}, "
          f"fixed_section dùng={fixed_used}) | tokens in/out: "
          f"{rec.usage.get('input')}/{rec.usage.get('output')} | {secs}s",
          f"- retry reasons: {retries or 'none'}  (F2 merge-fail count: {f2_count})",
          f"- self_review: `{_j((entry or {}).get('self_review')) if entry else '-'}`",
          "\n## SOURCE section (gốc)\n", _section_md(source)]
    if entry and entry.get("section"):
        md += ["\n## GENERATED section (sinh ra)\n", _section_md(entry["section"])]
    md += ["\n## AI calls (prompt thực gửi + response thô)\n", _calls_md(rec.calls)]

    path = os.path.join(OUT, tag + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    rows.append({
        "tag": tag, "sid": sid[:8], "k": k, "ver": ver,
        "status": "OK" if entry else "FAILED", "error": error,
        "mode": (entry or {}).get("mode", "rewrite" if ver == "v2" else "-"),
        "topic": (entry or {}).get("topic", "-"),
        "trigram": (entry or {}).get("trigram_overlap_pct", "-"),
        "vmax": ((entry or {}).get("verbatim_overlap") or {}).get("max", "-"),
        "vavg": ((entry or {}).get("verbatim_overlap") or {}).get("weighted_avg", "-"),
        "calls": len(rec.calls), "f2": f2_count,
        "tok": f"{rec.usage.get('input')}/{rec.usage.get('output')}",
        "secs": secs,
    })
    print(f"  done {tag}: {rows[-1]['status']} mode={rows[-1]['mode']} "
          f"trigram={rows[-1]['trigram']} calls={rows[-1]['calls']}", flush=True)


async def main():
    os.makedirs(OUT, exist_ok=True)
    from config.database import init_db_pool, close_db_pool, get_db_pool
    await init_db_pool()
    rows = []
    try:
        from services.exam_generation_service import exam_generation_service as svc
        # clear skill-map cache for target sections so the FIRST v3 run logs
        # the real ANALYZE call (later runs demonstrate the cache hit)
        async with get_db_pool().acquire() as conn:
            await conn.execute(
                "DELETE FROM public.section_skill_maps WHERE section_id = ANY($1::uuid[])",
                SECTIONS)
        for sid in SECTIONS:
            source, _ctx = await svc.load_section_for_gen(sid)
            for k in KS:
                for ver in VERSIONS:
                    print(f"[run] {sid[:8]} k={k} {ver} ...", flush=True)
                    await run_one(svc, sid, k, ver, source, rows)
    finally:
        await close_db_pool()

    hdr = "| run | ver | K | status | mode | topic | trigram% | bigram max/avg | calls | F2 | tokens | secs |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines = ["# SUMMARY — v2 vs v3 (MC reading, sonnet)\n",
             "trigram% = trùng câu chữ material với đề gốc (chỉ spec mode, chặn >10%); "
             "bigram = shadow metric mọi mode (1.0 = clone).\n", hdr, sep]
    for r in rows:
        lines.append(
            f"| {r['tag']} | {r['ver']} | {r['k']} | {r['status']} | {r['mode']} "
            f"| {str(r['topic'])[:30]} | {r['trigram']} | {r['vmax']}/{r['vavg']} "
            f"| {r['calls']} | {r['f2']} | {r['tok']} | {r['secs']} |")
    fails = [r for r in rows if r["status"] != "OK"]
    lines.append(f"\n- Failures: {[(r['tag'], r['error']) for r in fails] or 'NONE'}")
    lines.append(f"- Tổng F2 (fixed_section merge-fail đốt vòng generate): "
                 f"{sum(r['f2'] for r in rows)}")
    lines.append("\nMỗi run có file chi tiết cùng thư mục: source + prompts + "
                 "raw responses + generated section.")
    with open(os.path.join(OUT, "SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n" + "\n".join(lines))


asyncio.run(main())
