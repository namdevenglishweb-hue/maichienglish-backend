"""Pure helpers for exam-gen v3 spec mode (design docs/exam-gen-v3-spec-mode/).

Everything here is deterministic, DB-free and AI-free — the Tầng-B-style code
layer of spec mode: eligibility (core assignment inputs), source blocklist +
leak check, trigram similarity guard, source hashing for the skill-map cache,
structure facts derived from the source, and the word-count check.

Ported from the client's validated lib/exam-gen/{leakCheck,postprocess,
validator}.ts. Known, accepted limitations (design §6.3): the blocklist
heuristic is English-ASCII (accented proper nouns like "Hương" are not
caught — do NOT chase JS parity in tests); synonyms are not catchable in
code (the ANALYZE prompt rule is the defence).
"""

import hashlib
import json
import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Eligibility — two layers (design §2/F1):
#   orchestration gate (K, level)  — shared by every future core
#   core eligibility (per type)    — this round: multiple_choice only
# ---------------------------------------------------------------------------

SPEC_MIN_K = 3
SPEC_LEVELS = ("KET", "PET")


def orchestration_gate(k: int, level: Optional[str]) -> Optional[str]:
    """None = pass; else the human-readable reason spec mode is skipped."""
    if k < SPEC_MIN_K:
        return f"k={k} < {SPEC_MIN_K}"
    if level not in SPEC_LEVELS:
        return f"level {level!r} not in {SPEC_LEVELS}"
    return None


def mc_core_eligibility(section: dict[str, Any]) -> Optional[str]:
    """MC-core eligibility (design §3.3-3.6). None = eligible; else reason."""
    if section.get("type") not in (None, "multiple_choice"):
        return f"section type {section.get('type')!r} is not plain multiple_choice"
    mats = section.get("materials") or []
    if len(mats) != 1 or not isinstance(mats[0], dict) or mats[0].get("type") != "text":
        got = (f"{len(mats)} materials" if len(mats) != 1
               else f"1 {mats[0].get('type') if isinstance(mats[0], dict) else '?'} material")
        return f"needs exactly 1 text material (got {got})"
    if not (mats[0].get("content") or "").strip():
        return "text material is empty"
    qs = section.get("questions") or []
    if not qs:
        return "no questions"
    option_counts = set()
    for i, q in enumerate(qs):
        if q.get("question_type") != "multiple_choice":
            return f"question {i + 1} is {q.get('question_type')!r}, not multiple_choice"
        opts = (q.get("question_data") or {}).get("options") or []
        if len(opts) < 2:
            return f"question {i + 1} has fewer than 2 options"
        for o in opts:
            if not isinstance(o, dict) or not (o.get("text") or "").strip() or o.get("image_url"):
                return f"question {i + 1} has a non-text option (picture-MC not supported)"
        option_counts.add(len(opts))
    if len(option_counts) != 1:
        return f"mixed option counts {sorted(option_counts)}"
    return None


def assign_core(section: dict[str, Any], k: int, level: Optional[str]) -> Optional[str]:
    """Thin external assigner (hosted by generate_one_section, design §2).

    Returns the core name to run spec mode with, or None → rewrite fallback.
    The spec engine itself never routes — it RECEIVES the core.
    """
    if orchestration_gate(k, level) is not None:
        return None
    if mc_core_eligibility(section) is not None:
        return None
    return "multiple_choice"


# ---------------------------------------------------------------------------
# Blocklist + leak check (design §6.3, port of leakCheck.ts)
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset("""
a about above after again all also an and any are as at be because been before
being below between both but by can could did do does doing down during each
few first for from further get had has have having he her here hers him his
how i if in into is it its just know last like lot lots made make many maybe
me more most much my never new next no not now of off often on once one only
or other our out over own people rarely really same see she so some such than
that the their them then there these they thing things think this those
through time to too under until up us very want was we were what when where
which while who why will with would you your
writer writers question questions answer answers text paragraph option options
say says main trying feel
correct incorrect true false right wrong statement statements
position reading listening detail details global single specific structure
narrative article blog genre skill skills level candidate candidates factual
information inference
""".split())
# Line 3: exam-MECHANIC words added 2026-06-12 after a live run — in
# True/False MC sections the option texts "Correct"/"Incorrect" repeat for
# every question, enter the top-15 frequency list, and a skill map literally
# cannot describe that format without them → deterministic ANALYZE_DOMAIN_LEAK.
# Lines 4-6: skill-map SCHEMA vocabulary (audit finding #1) — emit_skill_map
# forces words like `position` (required key) and `reading`/`detail`/`global`
# (mandatory skill/scope values) into EVERY skill map; if a source passage
# happens to use them >=2 times they'd enter the blocklist and the leak check
# could never pass. They describe HOW, not WHAT — never real domain leaks.

_WORD_TOKEN = re.compile(r"[\w'\-]+", re.ASCII)  # English-ASCII heuristic (accepted)
_PROPER = re.compile(r"^[A-Z][a-z]")


def _section_source_text(section: dict[str, Any]) -> str:
    """Blocklist input (design §6.3): text-material content + stems + option
    text ONLY — instructions/part_label are EXCLUDED (meta-words like
    'choose' would poison the frequency list)."""
    parts: list[str] = []
    for m in section.get("materials") or []:
        if isinstance(m, dict) and m.get("type") == "text" and m.get("content"):
            parts.append(str(m["content"]))
    for q in section.get("questions") or []:
        qd = q.get("question_data") if isinstance(q, dict) else None
        if not isinstance(qd, dict):
            continue
        if qd.get("stem"):
            parts.append(str(qd["stem"]))
        for o in qd.get("options") or []:
            if isinstance(o, dict) and o.get("text"):
                parts.append(str(o["text"]))
    return " ".join(parts)


def build_blocklist(section: dict[str, Any]) -> list[str]:
    """Proper nouns (capitalized, not sentence-initial) + top-15 frequent
    content nouns (>=4 chars, >=2 occurrences, stopwords excluded)."""
    text = _section_source_text(section)
    block: set[str] = set()

    for sentence in re.split(r"[.!?\n]+", text):
        tokens = sentence.strip().split()
        for tok in tokens[1:]:  # skip sentence-initial token
            w = re.sub(r"[^\w'\-]", "", tok, flags=re.ASCII)
            if _PROPER.match(w) and w.lower() not in _STOPWORDS:
                block.add(w.lower())

    freq: dict[str, int] = {}
    for raw in _WORD_TOKEN.findall(re.sub(r"[^\w\s'\-]", " ", text.lower(), flags=re.ASCII)):
        w = raw.strip("'")
        if len(w) < 4 or w in _STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    top = sorted((item for item in freq.items() if item[1] >= 2),
                 key=lambda kv: -kv[1])[:15]
    block.update(w for w, _ in top)
    return sorted(block)


def _json_string_values(obj: Any, out: list[str]) -> None:
    """Collect every STRING VALUE in a JSON-like structure — keys excluded."""
    if isinstance(obj, dict):
        for v in obj.values():
            _json_string_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _json_string_values(v, out)
    elif isinstance(obj, str):
        out.append(obj)


def find_leaks(skill_map_json: str, blocklist: list[str]) -> list[str]:
    """Word-boundary, case-insensitive match over the skill map's string
    VALUES only (audit finding #1: scanning the raw JSON also matched
    mandatory schema KEYS like `position`, making innocent sources fail
    deterministically). Returns leaked terms (empty = clean); terms are
    regex-escaped. Falls back to the whole string when the input isn't JSON.
    """
    import json as _json
    try:
        values: list[str] = []
        _json_string_values(_json.loads(skill_map_json), values)
        haystack = " \n ".join(values).lower()
    except (ValueError, TypeError):
        haystack = skill_map_json.lower()  # defensive — caller always dumps JSON
    leaks = []
    for term in blocklist:
        if re.search(rf"\b{re.escape(term)}\b", haystack, re.IGNORECASE):
            leaks.append(term)
    return leaks


# ---------------------------------------------------------------------------
# Trigram similarity guard (design §9; enforce: >10% AND >=3 common trigrams)
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD_PERCENT = 10.0
SIMILARITY_MIN_COMMON = 3


def _trigrams(text: str) -> set:
    words = [w for w in re.sub(r"[^\w\s]", " ", (text or "").lower()).split() if w]
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)} if len(words) >= 3 else set()


def trigram_overlap(generated: str, source: str) -> tuple[float, int]:
    """(overlap percent of GENERATED trigrams found in source, common count).
    Asymmetric like the client's: denominator = generated trigram count."""
    g = _trigrams(generated)
    if not g:
        return 0.0, 0
    common = len(g & _trigrams(source))
    return 100.0 * common / len(g), common


def similarity_violation(generated_text: str, source_text: str) -> Optional[str]:
    """None = ok; else a NUMBERS-ONLY failure message (never quotes text —
    retry_error is rendered into the next prompt, design §4/M3)."""
    pct, common = trigram_overlap(generated_text, source_text)
    if pct > SIMILARITY_THRESHOLD_PERCENT and common >= SIMILARITY_MIN_COMMON:
        return (f"generated material is too similar to the source "
                f"(trigram overlap {pct:.1f}%, {common} common trigrams; "
                f"limit {SIMILARITY_THRESHOLD_PERCENT:.0f}%)")
    return None


# ---------------------------------------------------------------------------
# Skill-map cache hash (design §6.2/N6): hash EXACTLY what ANALYZE sees,
# canonical JSON — asyncpg jsonb does not preserve key order.
# ---------------------------------------------------------------------------


def section_source_hash(analyze_payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(analyze_payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Structure facts — derived in CODE from the source (design §4.4/N10).
# ANALYZE is trusted only for qualitative fields; a miscount would poison the
# cache and make every future generate fail its merge deterministically.
# ---------------------------------------------------------------------------


def derive_structure_facts(section: dict[str, Any], level: Optional[str]) -> dict[str, Any]:
    qs = section.get("questions") or []
    opts = (qs[0].get("question_data") or {}).get("options") or [] if qs else []
    from services.ai.topic_pool import LEVEL_TO_CEFR
    return {
        "exam_level": level,
        "cefr_level": LEVEL_TO_CEFR.get(level or "", "A2"),
        "skill": "reading",
        "section_type": "multiple_choice",
        "num_materials": len(section.get("materials") or []),
        "num_questions": len(qs),
        "options_per_question": len(opts),
    }


def merge_structure(skill_map: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Code-derived facts OVERRIDE whatever ANALYZE counted; ANALYZE keeps
    only its qualitative output (text_genre, word_count_range, style...)."""
    structure = dict(skill_map.get("structure") or {})
    structure.update(facts)
    out = dict(skill_map)
    out["structure"] = structure
    return out


def reshape_per_question(spec: dict[str, Any], n: int) -> dict[str, Any]:
    """Align spec['per_question'] length to `n` (= preset.num_questions) WITHOUT
    touching any prompt: the GENERATE template just dumps this list, so feeding
    it a list of length n keeps STRUCTURE SPEC (n questions) and PER-QUESTION
    SPEC consistent. ANALYZE still AUTHORS the content; this only reconciles the
    COUNT when the preset's question count differs from the source's.

    Shrink (n<m): sample evenly to keep skill variety. Grow (n>m): cycle the
    pattern. Positions are renumbered 1..n. No-op (besides renumber) when n==m
    or when there is no per_question."""
    pq = spec.get("per_question") or []
    if not pq or n <= 0:
        return spec
    m = len(pq)
    if m == n:
        idx = list(range(m))
    elif n < m:
        idx = [round(i * (m - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
    else:
        idx = [i % m for i in range(n)]
    new = [{**dict(pq[j]), "position": k + 1} for k, j in enumerate(idx)]
    out = dict(spec)
    out["per_question"] = new
    return out


# ---------------------------------------------------------------------------
# Word-count check (design §4.8/M9): code-enforced, ±15% slack like the
# client's validator.ts (the range is ANALYZE-estimated, not gospel).
# ---------------------------------------------------------------------------


def word_count_violation(material_text: str, word_count_range: Any) -> Optional[str]:
    """None = ok; else a numbers-only failure message."""
    if not isinstance(word_count_range, (list, tuple)) or len(word_count_range) != 2:
        return None  # no usable range from ANALYZE — skip the check
    # Models routinely emit JSON floats for integer schema fields (350.0) —
    # coerce instead of silently disabling the guard (review finding).
    if any(isinstance(x, bool) for x in word_count_range):
        return None
    try:
        lo, hi = (int(x) for x in word_count_range)
    except (TypeError, ValueError):
        return None
    if lo <= 0 or hi <= 0:
        return None
    n = len(re.findall(r"[\w'\-]+", material_text or ""))
    if n < lo * 0.85 or n > hi * 1.15:
        return (f"material word count {n} outside the allowed range "
                f"{lo}-{hi} (±15%)")
    return None
