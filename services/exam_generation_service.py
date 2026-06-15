"""AI exam generation — engine + two modes.

Design: docs/exam-ai-generation/exam-ai-generation-design.md.

Layering (§2):
  generate_one_section(...)        ← the shared core: AI rewrite → self-review
                                     (Tầng A) → structural validate (Tầng B) → retry
  generate_similar_exam(...)       Mode 1: loop the core over all sections,
                                     all-or-nothing, auto-saves a draft exam.
  generate_sections_preview(...)   Mode 2: loop the core, per-part status, no save.
  assemble_generated_exam(...)     Mode 2 Save: persist FE-assembled sections.

The model is never trusted: media url/type, question_type/points and section
type/max_audio_plays are re-imposed from the source (`_merge_generated_section`)
and every result is re-validated in code (`_validate_section_structure`).
"""

import logging
import random as _random
import re
from typing import Any, Awaitable, Callable, Optional

from services.exceptions import NotFoundError, ValidationError
from services.exam_service import exam_service
from services.question_service import _validate_question_data
from services.section_service import _validate_materials, validate_gap_markers
from services.section_type_prompt_service import section_type_prompt_service
from services.ai import prompts
from services.ai.generator import get_ai_generator

logger = logging.getLogger(__name__)

STRUCTURAL_RETRIES = 2  # re-generate attempts on top of the first (§9.2)
ProgressCb = Optional[Callable[[int, int], Awaitable[None]]]

# v3 spec provenance keys copied from a section report into job report /
# generation_meta (Mode 1) — docs/exam-gen-v3-spec-mode/ §11.
_SPEC_REPORT_KEYS = ("mode", "core", "topic", "diversity_seed",
                     "skill_map_hash", "trigram_overlap_pct", "part_code",
                     "eligibility_reason")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StructureMismatch(Exception):
    """A generated section broke a structural invariant (§4) — triggers retry."""


class SectionGenerationError(Exception):
    """A section could not be produced within its retry budget (§9.2)."""

    def __init__(self, message: str, *, review: Optional[dict] = None):
        super().__init__(message)
        self.review = review or {}


class GenerationAborted(Exception):
    """Mode 1 all-or-nothing abort — carries the partial report (§9.4)."""

    def __init__(self, reason: str, report: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.report = report


# ---------------------------------------------------------------------------
# Pure helpers — structural invariants (§4.3) + merge (§6.2)
# ---------------------------------------------------------------------------

_GAP_MARKER = re.compile(r"\{\{gap:(\d+)\}\}")


def _count_gaps(materials: list[dict[str, Any]]) -> int:
    n = 0
    for m in materials or []:
        if isinstance(m, dict) and m.get("type") == "text":
            n += len(_GAP_MARKER.findall(m.get("content") or ""))
    return n


def _assert_structure_preserved(
    original: dict[str, Any], generated: dict[str, Any]
) -> None:
    """Raise StructureMismatch if `generated` broke any invariant vs `original`.

    Media `url` must be byte-identical; `meta` may differ (§4.2). MC/matching
    keep option count and a valid `correct_index`. Gap-marker count is stable.
    """
    if original.get("type") != generated.get("type"):
        raise StructureMismatch("section type changed")
    if original.get("max_audio_plays") != generated.get("max_audio_plays"):
        raise StructureMismatch("max_audio_plays changed")

    om, gm = original.get("materials") or [], generated.get("materials") or []
    if len(om) != len(gm):
        raise StructureMismatch(f"material count {len(om)} -> {len(gm)}")
    for i, (a, b) in enumerate(zip(om, gm)):
        if a.get("type") != b.get("type"):
            raise StructureMismatch(f"materials[{i}] type changed")
        if a.get("type") in ("audio", "image") and a.get("url") != b.get("url"):
            raise StructureMismatch(f"materials[{i}] url must be preserved")

    oq, gq = original.get("questions") or [], generated.get("questions") or []
    if len(oq) != len(gq):
        raise StructureMismatch(f"question count {len(oq)} -> {len(gq)}")
    for i, (a, b) in enumerate(zip(oq, gq)):
        if a.get("question_type") != b.get("question_type"):
            raise StructureMismatch(f"questions[{i}] question_type changed")
        if a.get("points") != b.get("points"):
            raise StructureMismatch(f"questions[{i}] points changed")
        if a.get("question_type") in ("multiple_choice", "matching"):
            ao = (a.get("question_data") or {}).get("options") or []
            bo = (b.get("question_data") or {}).get("options") or []
            if len(ao) != len(bo):
                raise StructureMismatch(f"questions[{i}] option count changed")
            ci = (b.get("question_data") or {}).get("correct_index")
            if not isinstance(ci, int) or ci < 0 or ci >= len(bo):
                raise StructureMismatch(f"questions[{i}] correct_index out of range")

    if _count_gaps(om) != _count_gaps(gm):
        raise StructureMismatch("{{gap:N}} marker count changed")


def _merge_generated_section(
    source: dict[str, Any], ai_out: dict[str, Any], *, strict_spec: bool = False,
    preset: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge AI content onto the source, re-imposing all hard invariants.

    Returns (merged_section, justifications). Raises StructureMismatch when the
    AI returned the wrong number of materials/questions (cannot merge by index).

    `strict_spec` (v3 spec mode, design §4.7/N13): missing material content /
    instructions / part_label ⇒ FAIL instead of falling back to the source —
    a fallback would splice source text/flavour into a "brand-new" section.

    `preset` (part-presets, MC-only): when given, the QUESTION structure
    (count / question_type / points) is forced from the PRESET, not the source
    — "preset quyết cấu trúc, đề gốc bao nhiêu câu cũng kệ". The single text
    material is still taken from the AI output (MC reading = 1 text). When None,
    behaviour is exactly as before (source-driven) — fully backward compatible.
    """
    src_mats = source.get("materials") or []
    ai_mats = ai_out.get("materials") or []
    if len(ai_mats) != len(src_mats):
        raise StructureMismatch(
            f"expected {len(src_mats)} materials, got {len(ai_mats)}"
        )
    out_mats: list[dict[str, Any]] = []
    for sm, am in zip(src_mats, ai_mats):
        am = am if isinstance(am, dict) else {}
        t = sm.get("type")
        if t == "text":
            if strict_spec and not (am.get("content") or "").strip():
                raise StructureMismatch("materials missing content (no source fallback in spec mode)")
            m: dict[str, Any] = {
                "type": "text",
                "content": am.get("content") or sm.get("content") or "",
            }
            label = am.get("label") if strict_spec else am.get("label", sm.get("label"))
            if label:
                m["label"] = label
        elif t in ("audio", "image"):
            m = {"type": t, "url": sm.get("url")}  # url FORCED from source
            label = am.get("label", sm.get("label"))
            if label:
                m["label"] = label
            am_meta = am.get("meta") if isinstance(am.get("meta"), dict) else {}
            sm_meta = sm.get("meta") if isinstance(sm.get("meta"), dict) else {}
            if t == "image":
                alt = am.get("alt", sm.get("alt"))
                if alt:
                    m["alt"] = alt
                m["meta"] = {
                    "description": am_meta.get("description") or sm_meta.get("description"),
                    "pendingReplacement": True,
                }
            else:
                m["meta"] = {
                    "transcript": am_meta.get("transcript") or sm_meta.get("transcript"),
                    "pendingReplacement": True,
                }
        else:
            raise StructureMismatch(f"unknown source material type {t!r}")
        out_mats.append(m)

    ai_qs = ai_out.get("questions") or []
    out_qs: list[dict[str, Any]] = []
    justifications: list[dict[str, Any]] = []
    if preset is not None:
        # PRESET drives the question structure (count/type/points), not source.
        if len(ai_qs) != preset.num_questions:
            raise StructureMismatch(
                f"expected {preset.num_questions} questions "
                f"(preset {preset.part_code}), got {len(ai_qs)}"
            )
        for i, aq in enumerate(ai_qs):
            aq = aq if isinstance(aq, dict) else {}
            qd = aq.get("question_data")
            if not isinstance(qd, dict):
                raise StructureMismatch(f"questions[{i}] missing question_data")
            pos = i + 1
            out_qs.append({
                "position": pos,
                "question_type": preset.question_type,        # FORCED from preset
                "points": preset.points_per_question,         # FORCED from preset
                "question_data": qd,
            })
            if aq.get("answer_justification"):
                justifications.append(
                    {"position": pos, "justification": aq["answer_justification"]}
                )
    else:
        src_qs = source.get("questions") or []
        if len(ai_qs) != len(src_qs):
            raise StructureMismatch(
                f"expected {len(src_qs)} questions, got {len(ai_qs)}"
            )
        for i, (sq, aq) in enumerate(zip(src_qs, ai_qs)):
            aq = aq if isinstance(aq, dict) else {}
            qd = aq.get("question_data")
            if not isinstance(qd, dict):
                raise StructureMismatch(f"questions[{i}] missing question_data")
            pos = sq.get("position", i + 1)
            out_qs.append({
                "position": pos,
                "question_type": sq.get("question_type"),  # FORCED
                "points": sq.get("points", 1),             # FORCED
                "question_data": qd,
            })
            if aq.get("answer_justification"):
                justifications.append(
                    {"position": pos, "justification": aq["answer_justification"]}
                )

    if strict_spec and not (
        (ai_out.get("part_label") or "").strip()
        and (ai_out.get("instructions") or "").strip()
    ):
        raise StructureMismatch(
            "part_label/instructions missing (no source fallback in spec mode)"
        )
    merged = {
        "type": preset.section_type if preset is not None else source.get("type"),
        "part_label": ai_out.get("part_label") or source.get("part_label"),
        "instructions": ai_out.get("instructions") or source.get("instructions"),
        "max_audio_plays": None if preset is not None else source.get("max_audio_plays"),
        "materials": out_mats,
        "questions": out_qs,
    }
    return merged, justifications


def _validate_section_structure(source: dict[str, Any], merged: dict[str, Any]) -> None:
    """Tầng B (§8): code validators + structural-invariant checker."""
    mats = _validate_materials(merged["materials"])
    positions: set[int] = set()
    for q in merged["questions"]:
        _validate_question_data(q["question_type"], q["question_data"])
        positions.add(q["position"])
    validate_gap_markers(mats, positions, section_label="generated section")
    _assert_structure_preserved(source, merged)


def _assert_source_media_meta(sections: list[dict[str, Any]]) -> None:
    """Precondition (§5.3): every audio needs meta.transcript, every image
    needs meta.description. Raise ValidationError listing what's missing."""
    missing: list[str] = []
    for s in sections:
        for mi, m in enumerate(s.get("materials") or []):
            if not isinstance(m, dict):
                continue
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
            if m.get("type") == "audio" and not (meta.get("transcript") or "").strip():
                missing.append(f"section {s.get('position')} material {mi} (audio) missing transcript")
            elif m.get("type") == "image" and not (meta.get("description") or "").strip():
                missing.append(f"section {s.get('position')} material {mi} (image) missing description")
    if missing:
        raise ValidationError(
            "source exam not ready for generation — fill media meta first: "
            + "; ".join(missing)
        )


def _validate_k(k: Any) -> None:
    if not isinstance(k, int) or isinstance(k, bool) or not (prompts.MIN_K <= k <= prompts.MAX_K):
        raise ValidationError(f"k must be an integer in [{prompts.MIN_K},{prompts.MAX_K}]")


def _validate_prompt_version(version: Optional[str]) -> str:
    """Resolve + validate a promptVersion (None → default). 400 at the route."""
    try:
        return prompts.get_prompt_version(version).name
    except ValueError as e:
        raise ValidationError(str(e))


# ---------------------------------------------------------------------------
# Verbatim-overlap metric — SHADOW MODE ONLY (pure, no AI, no enforcement).
# Word-bigram Jaccard between the source's and the generated section's
# content-bearing text (passages, transcripts, descriptions, stems+options).
# Scaffolding ({{gap:N}} markers, urls, counts) is excluded — it must stay
# identical by design and would inflate the score. Recorded in reports +
# generation_meta so v1/v2 A/B runs produce comparable numbers; thresholds/
# enforcement are deliberately NOT here yet (pending real-data tuning).
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-zA-Z']+")


def _safe_text(value: Any) -> str:
    """jsonb fields are untrusted — anything non-string counts as empty."""
    return value if isinstance(value, str) else ""


def _bigram_set(text: Any) -> set:
    words = _WORD.findall(_GAP_MARKER.sub(" ", _safe_text(text).lower()))
    if len(words) < 2:
        return set(words)
    return {(words[i], words[i + 1]) for i in range(len(words) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_verbatim_overlap(
    source: dict[str, Any], generated: dict[str, Any]
) -> dict[str, Any]:
    """Per-field overlap [0..1] + summary. 1.0 = verbatim copy of the source."""
    pairs: list[tuple[str, Optional[str], Optional[str]]] = []
    sm, gm = source.get("materials") or [], generated.get("materials") or []
    for i, (s, g) in enumerate(zip(sm, gm)):
        if not (isinstance(s, dict) and isinstance(g, dict)):
            continue
        t = s.get("type")
        if t == "text":
            pairs.append((f"materials[{i}].content", s.get("content"), g.get("content")))
        elif t == "audio":
            pairs.append((
                f"materials[{i}].meta.transcript",
                (s.get("meta") or {}).get("transcript"),
                (g.get("meta") or {}).get("transcript"),
            ))
        elif t == "image":
            pairs.append((
                f"materials[{i}].meta.description",
                (s.get("meta") or {}).get("description"),
                (g.get("meta") or {}).get("description"),
            ))

    def _question_text(q: dict[str, Any]) -> str:
        qd = q.get("question_data") if isinstance(q.get("question_data"), dict) else {}
        parts = [_safe_text(qd.get("stem"))]
        for o in qd.get("options") or []:  # MC + matching (shared shape)
            if isinstance(o, dict):
                parts.append(_safe_text(o.get("text")))
        # fill_blank / form_completion: answers + per-blank presentation text
        for ans in qd.get("correct_answers") or []:
            parts.append(_safe_text(ans))
        for key in ("label", "prefix", "postfix"):
            parts.append(_safe_text(qd.get(key)))
        return " ".join(p for p in parts if p)

    sq, gq = source.get("questions") or [], generated.get("questions") or []
    for i, (s, g) in enumerate(zip(sq, gq)):
        if isinstance(s, dict) and isinstance(g, dict):
            pairs.append((f"questions[{i}]", _question_text(s), _question_text(g)))

    fields: list[dict[str, Any]] = []
    weighted = 0.0
    total_w = 0
    mx = 0.0
    for path, a, b in pairs:
        src_words = len(_WORD.findall(_safe_text(a).lower()))
        if src_words == 0:
            # nothing measurable on the source side — recording a 0.0 with
            # weight would silently dilute weighted_avg (review finding H2)
            continue
        ov = _jaccard(_bigram_set(a), _bigram_set(b))
        fields.append({"path": path, "overlap": round(ov, 3), "src_words": src_words})
        weighted += ov * src_words
        total_w += src_words
        mx = max(mx, ov)
    return {
        "max": round(mx, 3),
        "weighted_avg": round(weighted / total_w, 3) if total_w else 0.0,
        "fields": fields,
    }


def _normalize_section_positions(section: dict[str, Any]) -> dict[str, Any]:
    """Renumber active questions to a contiguous 1..N and remap `{{gap:N}}`
    markers in text materials to match.

    Source questions may carry non-contiguous positions (e.g. 1,3,4 after a
    granular soft-delete). `create_exam_nested` re-assigns positions 1..N by
    array order and validates gap markers against that — so we must align the
    source (and its gap markers) to 1..N up front, else a generated fill_blank
    section would be rejected at persist time. Mutates + returns `section`.
    """
    qs = section.get("questions") or []
    old_to_new = {q.get("position"): i + 1 for i, q in enumerate(qs)}
    for i, q in enumerate(qs):
        q["position"] = i + 1
    if any(old != new for old, new in old_to_new.items()):
        def _remap(match: "re.Match") -> str:
            n = int(match.group(1))
            return "{{gap:%d}}" % old_to_new.get(n, n)
        for m in section.get("materials") or []:
            if isinstance(m, dict) and m.get("type") == "text" and m.get("content"):
                m["content"] = _GAP_MARKER.sub(_remap, m["content"])
    return section


def _media_todos(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # section_position = 1..N by array order — matches the positions
    # create_exam_nested assigns to the NEW exam (merged sections carry no
    # `position` key of their own).
    todos: list[dict[str, Any]] = []
    for si, s in enumerate(sections):
        if not isinstance(s, dict):
            continue
        for mi, m in enumerate(s.get("materials") or []):
            # meta is FE-supplied on the assemble path — a non-dict truthy
            # meta must not 500 before validation gets to reject it
            meta = m.get("meta") if isinstance(m, dict) else None
            if isinstance(meta, dict) and meta.get("pendingReplacement"):
                todos.append({
                    "section_position": si + 1,
                    "material_index": mi,
                    "media_type": m.get("type"),
                })
    return todos


# ---------------------------------------------------------------------------
# Balanced answer-key shuffle — post-process by CODE, never by prompt.
# Ported from the client amendment §6 (maichienglish-feature-ai-exam-generation,
# lib/exam-gen/postprocess.ts): models cluster keys (A-A-A-A-A observed; a
# prompt rule still clustered 3/5), and plain per-question Fisher-Yates still
# puts 3/5 keys on one position ~37% of the time. So: rejection-sample target
# key positions under a balance cap of ceil(n_questions/n_options) (5q/4opt =
# max 2 per position), then Fisher-Yates the distractors into the other slots.
# answer_justification references content, not positions — unaffected.
# ---------------------------------------------------------------------------


def shuffle_answer_keys(
    section: dict[str, Any], *, rng: Optional[_random.Random] = None
) -> dict[str, Any]:
    """Reorder MC options in-place with a balanced key distribution.

    Scope (by request): question_type == 'multiple_choice' ONLY — matching /
    fill_blank / form_completion / writing / speaking are never touched.
    For `multiple_choice_shared` sections every question shows the SAME
    option table (the FE detects this via identical lists), so a single
    shared permutation is applied instead of per-question shuffles.
    Answers are preserved: only positions + correct_index move together.
    """
    rng = rng or _random
    qds = []
    for q in section.get("questions") or []:
        if not isinstance(q, dict):
            continue
        qd = q.get("question_data")
        if (
            q.get("question_type") == "multiple_choice"
            and isinstance(qd, dict)
            and isinstance(qd.get("options"), list)
            and len(qd["options"]) >= 2
            and isinstance(qd.get("correct_index"), int)
            and not isinstance(qd.get("correct_index"), bool)
            and 0 <= qd["correct_index"] < len(qd["options"])
        ):
            qds.append(qd)
    if not qds:
        return section

    if section.get("type") == "multiple_choice_shared":
        first = qds[0]["options"]
        if all(qd["options"] == first for qd in qds):
            # One permutation for the whole shared table.
            perm = list(range(len(first)))
            rng.shuffle(perm)
            shared = [first[i] for i in perm]
            new_index_of_old = {old: new for new, old in enumerate(perm)}
            for qd in qds:
                qd["options"] = list(shared)
                qd["correct_index"] = new_index_of_old[qd["correct_index"]]
            return section
        # degenerate shared section (differing lists) → per-question below

    # Sample target key positions PER GROUP of equal option count. Sampling
    # in range(max_options) and folding with `% len(opts)` skews and clusters
    # the shorter lists (review finding: 4×2-option keys landed on one
    # position in 66/2000 runs). Per-group sampling needs no modulo; for the
    # normal uniform-count section there is exactly one group, so behaviour
    # is unchanged.
    groups: dict[int, list[dict[str, Any]]] = {}
    for qd in qds:
        groups.setdefault(len(qd["options"]), []).append(qd)

    for n_opts, group in groups.items():
        n = len(group)
        cap = -(-n // n_opts)  # ceil — always satisfiable (cap*n_opts >= n)
        targets: list[int] = []
        for _ in range(1000):  # rejection sampling; loop is a seatbelt
            candidate = [rng.randrange(n_opts) for _ in range(n)]
            counts = [0] * n_opts
            for t in candidate:
                counts[t] += 1
            if max(counts) <= cap:
                targets = candidate
                break
        else:  # statistically unreachable (~0.37^1000) — keep property visible
            logger.warning("shuffle_answer_keys: balance not reached in 1000 tries")
            targets = candidate

        for qd, t in zip(group, targets):
            opts = qd["options"]
            correct = opts[qd["correct_index"]]
            distractors = [o for i, o in enumerate(opts) if i != qd["correct_index"]]
            rng.shuffle(distractors)
            merged_opts, d = [], 0
            for i in range(len(opts)):
                if i == t:
                    merged_opts.append(correct)
                else:
                    merged_opts.append(distractors[d])
                    d += 1
            qd["options"] = merged_opts
            qd["correct_index"] = t
    return section


# ---------------------------------------------------------------------------
# v3 SPEC MODE — skill-map cache + the spec engine
# (docs/exam-gen-v3-spec-mode/exam-gen-v3-spec-mode-design.md §4-§8)
# ---------------------------------------------------------------------------

ANALYZE_RETRIES = 2  # re-run ANALYZE on leak, on top of the first attempt
# Spec-capable versions fall back to this registry entry for ineligible
# sections (docs §10.4). Single definition so a registry rename can't 500.
REWRITE_FALLBACK_VERSION = "v2"


def _analyze_view(section: dict[str, Any]) -> dict[str, Any]:
    """Content view of the source for ANALYZE (ids dropped — they don't
    affect content and would churn the cache hash on re-import)."""
    return {
        "type": section.get("type"),
        "part_label": section.get("part_label"),
        "instructions": section.get("instructions"),
        "materials": section.get("materials") or [],
        "questions": [{
            "position": q.get("position"),
            "question_type": q.get("question_type"),
            "question_data": q.get("question_data"),
            "points": q.get("points"),
        } for q in section.get("questions") or []],
    }


class SkillMapCache:
    """CRUD over section_skill_maps (migration 0023). Hash-keyed lazy
    invalidation; concurrent analyze+upsert = last write wins (accepted)."""

    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def get(self, section_id: str, source_hash: str) -> Optional[dict[str, Any]]:
        import json as _json
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT skill_map FROM public.section_skill_maps "
                "WHERE section_id = $1 AND source_hash = $2",
                section_id, source_hash,
            )
        if not row:
            return None
        raw = row["skill_map"]
        return _json.loads(raw) if isinstance(raw, str) else raw

    async def upsert(self, section_id: str, skill_map: dict[str, Any],
                     source_hash: str, model: Optional[str]) -> None:
        import json as _json
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.section_skill_maps
                    (section_id, skill_map, source_hash, model)
                VALUES ($1, $2::jsonb, $3, $4)
                ON CONFLICT (section_id) DO UPDATE SET
                    skill_map = EXCLUDED.skill_map,
                    source_hash = EXCLUDED.source_hash,
                    model = EXCLUDED.model,
                    updated_at = now()
                """,
                section_id, _json.dumps(skill_map), source_hash, model,
            )


skill_map_cache = SkillMapCache()


async def _get_or_analyze_skill_map(
    source_section: dict[str, Any],
    scrub_ctx: dict[str, Any],
    generator,
    version: str,
    cache: SkillMapCache,
    core: str = "multiple_choice",
) -> tuple[dict[str, Any], str]:
    """Cache lookup → ANALYZE → leak check (budget 1+2, separate from the
    generate budget) → upsert. Raises SectionGenerationError on
    ANALYZE_DOMAIN_LEAK (a dirty skill map must never be used OR cached).
    `core` selects the ANALYZE prompt (mc vs cloze) and is part of the cache key
    so a section analyzed under different cores can't collide."""
    import json as _json
    from services.ai import spec_mode

    view = _analyze_view(source_section)
    analyze_payload: dict[str, Any] = {
        "prompt_version": version, "core": core,
        "exam_context": scrub_ctx, "section": view,
    }
    source_hash = spec_mode.section_source_hash(
        {"exam_context": scrub_ctx, "section": view, "core": core})

    blocklist = spec_mode.build_blocklist(source_section)
    section_id = source_section.get("id")
    if section_id:
        cached = await cache.get(str(section_id), source_hash)
        # Re-run the (free, pure) leak check on cache hits too — a map cached
        # under an older/weaker blocklist heuristic must not bypass the
        # current one (review finding); a leaky hit is treated as a miss.
        if cached is not None and not spec_mode.find_leaks(
                _json.dumps(cached, ensure_ascii=False), blocklist):
            return cached, source_hash

    leaks: list[str] = []
    for _ in range(1 + ANALYZE_RETRIES):
        skill_map = await generator.analyze_section(analyze_payload)
        leaks = spec_mode.find_leaks(
            _json.dumps(skill_map, ensure_ascii=False), blocklist)
        if not leaks:
            if section_id:
                await cache.upsert(str(section_id), skill_map, source_hash,
                                   getattr(generator, "model", None))
            return skill_map, source_hash
        analyze_payload = {**analyze_payload, "leak_feedback": leaks}
    raise SectionGenerationError(
        f"ANALYZE_DOMAIN_LEAK: skill map still leaked {len(leaks)} source "
        f"term(s) after {1 + ANALYZE_RETRIES} attempts"
    )


def _spec_code_checks(
    section: dict[str, Any], spec: dict[str, Any], src_material: str,
    rng: Optional[_random.Random],
) -> None:
    """Design §4 steps 8-10: word-count → shuffle → trigram guard. Pure code,
    runs BEFORE verify (each failure costs 0 AI calls). Failure messages are
    numbers/labels only (M3 — they feed retry_error into the next prompt)."""
    from services.ai import spec_mode

    material_text = (section.get("materials") or [{}])[0].get("content") or ""
    err = spec_mode.word_count_violation(
        material_text, (spec.get("structure") or {}).get("word_count_range"))
    if err:
        raise StructureMismatch(err)
    shuffle_answer_keys(section, rng=rng)
    err = spec_mode.similarity_violation(material_text, src_material)
    if err:
        raise StructureMismatch(err)


def _grade_blind_solve(
    section: dict[str, Any], per_question: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """AMENDMENT v1.2 §9.4 — CODE (not the model) decides answer correctness:
    compare each examiner_answer_index to the REAL correct_index, by position.
    Returns one 'critical' issue per mismatch (empty = examiner agrees with the
    key on every question)."""
    real: dict[Any, Any] = {}
    for q in section.get("questions") or []:
        if isinstance(q, dict):
            real[q.get("position")] = (q.get("question_data") or {}).get("correct_index")
    problems: list[dict[str, Any]] = []
    for item in per_question:
        pos = item.get("position")
        examiner = item.get("examiner_answer_index")
        key = real.get(pos)
        if examiner != key:
            problems.append({
                "severity": "critical",
                "question_position": pos,
                "problem": (f"blind examiner answered Q{pos} as option {examiner} "
                            f"but the key is option {key}"),
            })
    return problems


def _validate_per_question(
    section: dict[str, Any], per_question: Any
) -> None:
    """The blind-solve verdict MUST carry exactly one well-formed entry per
    question (§9.4): one entry per question POSITION (no missing/extra/dup —
    else _grade_blind_solve would silently leave a question ungraded or invent
    a false critical against a non-existent key), integer examiner_answer_index,
    NON-EMPTY evidence_quote. A malformed verdict can't be graded →
    StructureMismatch (numbers/labels only, M3), counted as a generate retry."""
    want = {q.get("position") for q in section.get("questions") or []}
    if not isinstance(per_question, list) or len(per_question) != len(want):
        got = len(per_question) if isinstance(per_question, list) else "none"
        raise StructureMismatch(
            f"blind-solve per_question count {got} != {len(want)} questions")
    seen: set[Any] = set()
    for item in per_question:
        if not isinstance(item, dict):
            raise StructureMismatch("blind-solve per_question entry not an object")
        pos = item.get("position")
        if pos not in want or pos in seen:
            raise StructureMismatch(
                f"blind-solve per_question position {pos!r} unknown or duplicated")
        seen.add(pos)
        idx = item.get("examiner_answer_index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise StructureMismatch(
                f"blind-solve Q{pos} has no integer answer index")
        if not (item.get("evidence_quote") or "").strip():
            raise StructureMismatch(
                f"blind-solve Q{pos} has an empty evidence quote")


async def _spec_verify(
    source_section: dict[str, Any],
    section: dict[str, Any],
    payload: dict[str, Any],
    k: int,
    generator,
    rounds: int,
    spec: dict[str, Any],
    src_material: str,
    rng: Optional[_random.Random],
    preset: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Spec-mode BLIND-SOLVE verify + FIX loop (AMENDMENT v1.2 §9.4/§9.5).

    Each round: the examiner solves the key-stripped section unaided → CODE
    grades its answers against the real key + collects model-reported issues →
    if clean, ACCEPT. Otherwise, if rounds remain, the FIX call (the only call
    shown the real key) returns a corrected section that is re-merged, re-run
    through the code checks (shuffle + trigram), and verified again next round.
    Rounds exhausted with criticals still standing → StructureMismatch → the
    caller starts a fresh GENERATE round."""
    if rounds <= 0:
        return section, {"rounds": 0, "final_issues": []}
    issues: list[dict[str, Any]] = []
    done = 0
    for round_i in range(rounds):
        verdict = await generator.verify_section(section, payload, k=k)
        done += 1
        per_question = verdict.get("per_question")
        _validate_per_question(section, per_question)  # raises → counts as retry
        model_issues = verdict.get("issues") or []
        key_problems = _grade_blind_solve(section, per_question)
        issues = model_issues + key_problems
        criticals = key_problems + [
            i for i in model_issues if i.get("severity") == "critical"]
        if not criticals:
            return section, {"rounds": done, "final_issues": issues}
        if round_i == rounds - 1:
            break  # no budget left for a FIX — fall through to raise
        # FIX round: hand the fixer the real key + the problems CODE found.
        fix_payload = {
            **payload,
            "fix_problems": [i.get("problem", "") for i in criticals],
        }
        fixed = await generator.fix_section(section, fix_payload, k=k)
        section, _ = _merge_generated_section(
            source_section, fixed, strict_spec=True, preset=preset)
        _spec_code_checks(section, spec, src_material, rng)  # raises on fail
    raise StructureMismatch(
        "blind-solve verify left critical issues after "
        f"{done} round(s): "
        + "; ".join(i.get("problem", "") for i in issues
                    if i.get("severity") == "critical")
    )


# ---------------------------------------------------------------------------
# mc_cloze core (PET_R_P5 / KET_R_P4). The AI emits a passage with each gap's
# target wrapped in [[i]]…[[i]] sentinels + per_gap{target,distractors}; CODE
# carves the sentinels into {{gap:N}}, builds the MC options, and validates.
# Orchestration (ANALYZE→leak→seed→generate→verify→Tầng B) is shared.
# ---------------------------------------------------------------------------

_CLOZE_MARKER = re.compile(r"\[\[(\d+)\]\]")


def _assemble_cloze_section(
    source: dict[str, Any], ai_out: dict[str, Any], preset: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a cloze section from emit_cloze output, forcing structure from the
    preset. Carves SINGLE numbered blank tokens [[i]] → {{gap:i}} (each used once,
    1..N); builds options = target + distractors (correct_index 0, shuffled later
    by code-checks). Raises StructureMismatch (numbers/labels only) on any
    malformed gap → counts as a generate retry."""
    n = preset.num_questions
    L = preset.options_per_question or 0
    per_gap = ai_out.get("per_gap")
    if not isinstance(per_gap, list) or len(per_gap) != n:
        got = len(per_gap) if isinstance(per_gap, list) else "none"
        raise StructureMismatch(f"expected {n} gaps, got {got}")
    by_pos: dict[int, dict[str, Any]] = {}
    for g in per_gap:
        if isinstance(g, dict):
            by_pos[g.get("position")] = g
    if sorted(by_pos) != list(range(1, n + 1)):
        raise StructureMismatch(f"gap positions must be 1..{n} (got {sorted(by_pos)})")

    text = ai_out.get("text") or ""
    if not text.strip():
        raise StructureMismatch("cloze text missing (no source fallback in spec mode)")
    # Validate the single blank tokens [[1]]..[[N]] — each EXACTLY ONCE.
    found = sorted(int(m) for m in _CLOZE_MARKER.findall(text))
    if found != list(range(1, n + 1)):
        raise StructureMismatch(
            f"text must contain blank tokens [[1]]..[[{n}]] each exactly once "
            f"(found {found or 'none'})")
    # Carve [[i]] → {{gap:i}}.
    out_text = _CLOZE_MARKER.sub(lambda m: "{{gap:%s}}" % m.group(1), text)

    out_qs: list[dict[str, Any]] = []
    justifications: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        g = by_pos[i]
        target = (g.get("target") or "").strip()
        distractors = [d for d in (g.get("distractors") or []) if (d or "").strip()]
        if not target:
            raise StructureMismatch(f"gap {i} missing target")
        if len(distractors) != L - 1:
            raise StructureMismatch(
                f"gap {i} needs {L - 1} distractors, got {len(distractors)}")
        options = [{"text": target}] + [{"text": d} for d in distractors]
        out_qs.append({
            "position": i,
            "question_type": preset.question_type,          # FORCED from preset
            "points": preset.points_per_question,           # FORCED from preset
            "question_data": {"stem": "", "options": options, "correct_index": 0},
        })
        if g.get("reason"):
            justifications.append({"position": i, "justification": g["reason"]})

    if not ((ai_out.get("part_label") or "").strip()
            and (ai_out.get("instructions") or "").strip()):
        raise StructureMismatch("part_label/instructions missing (no source fallback)")
    section = {
        "type": preset.section_type,
        "part_label": ai_out["part_label"],
        "instructions": ai_out["instructions"],
        "max_audio_plays": None,
        "materials": [{"type": "text", "content": out_text}],
        "questions": out_qs,
    }
    return section, justifications


def _spec_code_checks_cloze(
    section: dict[str, Any], spec: dict[str, Any], src_material: str,
    rng: Optional[_random.Random], preset: Any = None,
) -> None:
    """Cloze code checks: word-count → gap integrity (count==N, 1..N) → balanced
    shuffle → trigram guard. Pure code, runs BEFORE verify."""
    from services.ai import spec_mode

    material_text = (section.get("materials") or [{}])[0].get("content") or ""
    err = spec_mode.word_count_violation(
        material_text, (spec.get("structure") or {}).get("word_count_range"))
    if err:
        raise StructureMismatch(err)
    n = len(section.get("questions") or [])
    gaps = sorted(int(m) for m in _GAP_MARKER.findall(material_text))
    if gaps != list(range(1, n + 1)):
        raise StructureMismatch(
            f"cloze gap markers {gaps or 'none'} != 1..{n} questions")
    shuffle_answer_keys(section, rng=rng)
    err = spec_mode.similarity_violation(material_text, src_material)
    if err:
        raise StructureMismatch(err)


async def _spec_verify_cloze(
    source_section: dict[str, Any], section: dict[str, Any], payload: dict[str, Any],
    k: int, generator, rounds: int, spec: dict[str, Any], src_material: str,
    rng: Optional[_random.Random], preset: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cloze verify (strict, 2-pass): each round runs the blind solve TWICE
    (independent). ANY pass with a key mismatch OR a model 'critical' (incl. an
    ambiguous "more than one option fits" gap) → FIX (key-aware, re-assembled via
    the cloze carver) → re-check → next round. Clean on both passes → accept.
    Rounds exhausted with criticals → StructureMismatch (→ fresh GENERATE)."""
    if rounds <= 0:
        return section, {"rounds": 0, "final_issues": []}
    issues: list[dict[str, Any]] = []
    done = 0
    for round_i in range(rounds):
        criticals: list[dict[str, Any]] = []
        issues = []
        for _pass in range(2):                       # 2 independent blind solves
            verdict = await generator.verify_section(section, payload, k=k)
            per_question = verdict.get("per_question")
            _validate_per_question(section, per_question)
            model_issues = verdict.get("issues") or []
            key_problems = _grade_blind_solve(section, per_question)
            issues += model_issues + key_problems
            criticals += key_problems + [
                i for i in model_issues if i.get("severity") == "critical"]
        done += 1
        if not criticals:
            return section, {"rounds": done, "final_issues": issues}
        if round_i == rounds - 1:
            break
        fix_payload = {**payload,
                       "fix_problems": [i.get("problem", "") for i in criticals]}
        fixed = await generator.fix_section(section, fix_payload, k=k)
        section, _ = _assemble_cloze_section(source_section, fixed, preset)
        _spec_code_checks_cloze(section, spec, src_material, rng, preset)
    raise StructureMismatch(
        "cloze blind-solve left critical issues after "
        f"{done} round(s): "
        + "; ".join(i.get("problem", "") for i in issues
                    if i.get("severity") == "critical")
    )


# ---------------------------------------------------------------------------
# open_cloze core (PET_R_P6 / KET_R_P5). The AI emits a passage with single
# numbered blank tokens [[N]] + per_gap{answer, accepted_alternatives}; CODE
# carves the tokens into {{gap:N}}, builds fill_blank correct_answers accept-
# lists, and validates. The student TYPES a word (no options). The blind-solve
# examiner also TYPES a word (string) → CODE grades it against the accept-list;
# a valid-but-unlisted word is the signal to EXPAND the list (FIX), not a wrong
# key. Orchestration (ANALYZE→leak→seed→generate→verify→Tầng B) is shared.
# ---------------------------------------------------------------------------


def _norm_answer(s: Any) -> str:
    """Grading normal form for open-cloze answers: trim + casefold (matches
    utils.grading_utils._grade_fill_blank with case_sensitive=False)."""
    return s.strip().lower() if isinstance(s, str) else ""


def _assemble_open_cloze_section(
    source: dict[str, Any], ai_out: dict[str, Any], preset: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a fill_blank open-cloze section from emit_open_cloze output, forcing
    structure from the preset. Carves SINGLE numbered blank tokens [[i]] →
    {{gap:i}} (each used once, 1..N); builds each question's correct_answers =
    [answer] + accepted_alternatives (deduped, case-insensitive). Each answer is
    one word. Raises StructureMismatch (numbers/labels only) on any malformed gap
    → counts as a generate retry."""
    n = preset.num_questions
    per_gap = ai_out.get("per_gap")
    if not isinstance(per_gap, list) or len(per_gap) != n:
        got = len(per_gap) if isinstance(per_gap, list) else "none"
        raise StructureMismatch(f"expected {n} gaps, got {got}")
    by_pos: dict[int, dict[str, Any]] = {}
    for g in per_gap:
        if isinstance(g, dict):
            by_pos[g.get("position")] = g
    if sorted(by_pos) != list(range(1, n + 1)):
        raise StructureMismatch(f"gap positions must be 1..{n} (got {sorted(by_pos)})")

    text = ai_out.get("text") or ""
    if not text.strip():
        raise StructureMismatch("open-cloze text missing (no source fallback in spec mode)")
    found = sorted(int(m) for m in _CLOZE_MARKER.findall(text))
    if found != list(range(1, n + 1)):
        raise StructureMismatch(
            f"text must contain blank tokens [[1]]..[[{n}]] each exactly once "
            f"(found {found or 'none'})")
    out_text = _CLOZE_MARKER.sub(lambda m: "{{gap:%s}}" % m.group(1), text)

    out_qs: list[dict[str, Any]] = []
    justifications: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        g = by_pos[i]
        answer = (g.get("answer") or "").strip()
        if not answer:
            raise StructureMismatch(f"gap {i} missing answer")
        if len(answer.split()) != 1:
            raise StructureMismatch(f"gap {i} answer must be a single word")
        # accept-list = answer + alternatives, deduped case-insensitively, order
        # preserved (the primary answer stays first).
        accepted: list[str] = []
        seen: set[str] = set()
        for cand in [answer, *(g.get("accepted_alternatives") or [])]:
            cand = (cand or "").strip() if isinstance(cand, str) else ""
            if not cand or len(cand.split()) != 1:
                continue  # alternatives must also be single words; drop noise
            key = cand.lower()
            if key not in seen:
                seen.add(key)
                accepted.append(cand)
        out_qs.append({
            "position": i,
            "question_type": preset.question_type,          # FORCED from preset
            "points": preset.points_per_question,           # FORCED from preset
            "question_data": {"correct_answers": accepted, "case_sensitive": False},
        })
        if g.get("reason"):
            justifications.append({"position": i, "justification": g["reason"]})

    if not ((ai_out.get("part_label") or "").strip()
            and (ai_out.get("instructions") or "").strip()):
        raise StructureMismatch("part_label/instructions missing (no source fallback)")
    section = {
        "type": preset.section_type,
        "part_label": ai_out["part_label"],
        "instructions": ai_out["instructions"],
        "max_audio_plays": None,
        "materials": [{"type": "text", "content": out_text}],
        "questions": out_qs,
    }
    return section, justifications


def _spec_code_checks_open_cloze(
    section: dict[str, Any], spec: dict[str, Any], src_material: str,
    rng: Optional[_random.Random], preset: Any = None,
) -> None:
    """open-cloze code checks: word-count → gap integrity (count==N, 1..N) →
    trigram guard. No option shuffle (fill_blank has no options). Pure code,
    runs BEFORE verify."""
    from services.ai import spec_mode

    material_text = (section.get("materials") or [{}])[0].get("content") or ""
    err = spec_mode.word_count_violation(
        material_text, (spec.get("structure") or {}).get("word_count_range"))
    if err:
        raise StructureMismatch(err)
    n = len(section.get("questions") or [])
    gaps = sorted(int(m) for m in _GAP_MARKER.findall(material_text))
    if gaps != list(range(1, n + 1)):
        raise StructureMismatch(
            f"open-cloze gap markers {gaps or 'none'} != 1..{n} questions")
    err = spec_mode.similarity_violation(material_text, src_material)
    if err:
        raise StructureMismatch(err)


def _validate_per_question_str(section: dict[str, Any], per_question: Any) -> None:
    """Open-cloze blind-solve verdict shape: exactly one entry per question
    position (no missing/extra/dup), a NON-EMPTY string examiner_answer, and a
    NON-EMPTY evidence_quote. Malformed → StructureMismatch (counts as a retry)."""
    want = {q.get("position") for q in section.get("questions") or []}
    if not isinstance(per_question, list) or len(per_question) != len(want):
        got = len(per_question) if isinstance(per_question, list) else "none"
        raise StructureMismatch(
            f"blind-solve per_question count {got} != {len(want)} questions")
    seen: set[Any] = set()
    for item in per_question:
        if not isinstance(item, dict):
            raise StructureMismatch("blind-solve per_question entry not an object")
        pos = item.get("position")
        if pos not in want or pos in seen:
            raise StructureMismatch(
                f"blind-solve per_question position {pos!r} unknown or duplicated")
        seen.add(pos)
        if not (item.get("examiner_answer") or "").strip():
            raise StructureMismatch(
                f"blind-solve Q{pos} has an empty examiner_answer")
        if not (item.get("evidence_quote") or "").strip():
            raise StructureMismatch(
                f"blind-solve Q{pos} has an empty evidence quote")


def _grade_open_cloze_blind_solve(
    section: dict[str, Any], per_question: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """CODE grades the open-cloze blind solve: the examiner TYPED a word per gap;
    compare it (case-insensitive + trim) to that gap's accept-list. A typed word
    NOT in the accept-list is a 'critical' signal that the list may be incomplete
    (or the gap over-open) — FIX decides whether to ADD it or tighten the gap.
    Empty (= examiner agreed on every gap)."""
    accept: dict[Any, list[str]] = {}
    for q in section.get("questions") or []:
        if isinstance(q, dict):
            accept[q.get("position")] = (q.get("question_data") or {}).get("correct_answers") or []
    problems: list[dict[str, Any]] = []
    for item in per_question:
        pos = item.get("position")
        typed = item.get("examiner_answer")
        listed = accept.get(pos) or []
        if _norm_answer(typed) not in {_norm_answer(a) for a in listed}:
            problems.append({
                "severity": "critical",
                "question_position": pos,
                "problem": (f"blind examiner wrote {str(typed)!r} for gap {pos}, "
                            f"which is not in the accepted answers {listed}. If "
                            f"it is also correct here, add it; otherwise tighten "
                            f"the passage so only the intended word fits."),
            })
    return problems


async def _spec_verify_open_cloze(
    source_section: dict[str, Any], section: dict[str, Any], payload: dict[str, Any],
    k: int, generator, rounds: int, spec: dict[str, Any], src_material: str,
    rng: Optional[_random.Random], preset: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """open-cloze verify (strict, 2-pass): each round runs the type-a-word blind
    solve TWICE (independent) to gather alternatives. ANY pass where the examiner
    types a word outside the accept-list, OR a model 'critical' (over-open gap) →
    FIX (key-aware: ADD the word to the accept-list or tighten the gap) →
    re-assemble via the open-cloze carver → re-check → next round. Clean on both
    passes → accept. Rounds exhausted with criticals → StructureMismatch (→ fresh
    GENERATE)."""
    if rounds <= 0:
        return section, {"rounds": 0, "final_issues": []}
    issues: list[dict[str, Any]] = []
    done = 0
    for round_i in range(rounds):
        criticals: list[dict[str, Any]] = []
        issues = []
        for _pass in range(2):                       # 2 independent blind solves
            verdict = await generator.verify_section(section, payload, k=k)
            per_question = verdict.get("per_question")
            _validate_per_question_str(section, per_question)
            model_issues = verdict.get("issues") or []
            key_problems = _grade_open_cloze_blind_solve(section, per_question)
            issues += model_issues + key_problems
            criticals += key_problems + [
                i for i in model_issues if i.get("severity") == "critical"]
        done += 1
        if not criticals:
            return section, {"rounds": done, "final_issues": issues}
        if round_i == rounds - 1:
            break
        fix_payload = {**payload,
                       "fix_problems": [i.get("problem", "") for i in criticals]}
        fixed = await generator.fix_section(section, fix_payload, k=k)
        section, _ = _assemble_open_cloze_section(source_section, fixed, preset)
        _spec_code_checks_open_cloze(section, spec, src_material, rng, preset)
    raise StructureMismatch(
        "open-cloze blind-solve left critical issues after "
        f"{done} round(s): "
        + "; ".join(i.get("problem", "") for i in issues
                    if i.get("severity") == "critical")
    )


def _mc_assemble(source, ai_out, preset):
    return _merge_generated_section(source, ai_out, strict_spec=True, preset=preset)


def _mc_code_checks(section, spec, src_material, rng, preset=None):
    _spec_code_checks(section, spec, src_material, rng)


# Per-core ENGINE hooks. multiple_choice = existing functions (byte-identical).
CORE_ENGINE: dict[str, dict[str, Any]] = {
    "multiple_choice": {"assemble": _mc_assemble, "code_checks": _mc_code_checks,
                        "verify": _spec_verify},
    "mc_cloze": {"assemble": _assemble_cloze_section,
                 "code_checks": _spec_code_checks_cloze,
                 "verify": _spec_verify_cloze},
    "open_cloze": {"assemble": _assemble_open_cloze_section,
                   "code_checks": _spec_code_checks_open_cloze,
                   "verify": _spec_verify_open_cloze},
}


async def _generate_section_spec(
    source_section: dict[str, Any],
    k: int,
    *,
    core: str,
    exam_context: dict[str, Any],
    generator,
    section_prompt: Optional[str] = None,
    rounds: int = 2,
    version: str = "v3",
    rng: Optional[_random.Random] = None,
    cache: Optional[SkillMapCache] = None,
    preset: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The spec engine — core-agnostic. RECEIVES `core` (design §2) and pulls the
    per-core hooks from CORE_ENGINE (assemble / code_checks / verify); the
    orchestration (ANALYZE→leak→seed→generate→Tầng B) is shared. `core` is
    threaded into the payload so the adapters resolve the core's prompt set.

    `preset` (part-presets): when given, STRUCTURE comes from the PRESET (counts/
    options/word-count/CEFR) instead of the source, and per_question is reshaped
    to the preset count IN CODE. ANALYZE/leak/similarity/blind-solve unchanged —
    source is still analyzed for the skill map + leak baseline + similarity guard."""
    ce = CORE_ENGINE.get(core)
    if ce is None:
        raise SectionGenerationError(f"unknown spec core {core!r}")
    from services.ai import spec_mode
    from services.ai.topic_pool import pick_topic_and_seed
    from services import presets as presets_mod
    from services import preset_validator

    rng = rng or _random
    cache = cache or skill_map_cache
    level = exam_context.get("level")
    # B2: spec prompts may see ONLY level+skill — never title/description
    # (the source exam's title routinely names the very topic to hide).
    scrub_ctx = {"level": level, "skill": exam_context.get("skill")}

    skill_map, sm_hash = await _get_or_analyze_skill_map(
        source_section, scrub_ctx, generator, version, cache, core=core)
    if preset is not None:
        # PRESET is authoritative for structure; reshape ANALYZE's per_question
        # to the preset count in code (prompt template untouched).
        facts = presets_mod.structure_facts(preset)
        spec = spec_mode.merge_structure(skill_map, facts)
        spec = spec_mode.reshape_per_question(spec, preset.num_questions)
        structure_ref = presets_mod.preset_skeleton(preset)   # Tầng-B reference
    else:
        facts = spec_mode.derive_structure_facts(source_section, level)
        spec = spec_mode.merge_structure(skill_map, facts)
        structure_ref = source_section
    src_material = (source_section.get("materials") or [{}])[0].get("content") or ""

    last_err: Optional[str] = None
    review: dict[str, Any] = {"rounds": 0, "final_issues": []}
    for _ in range(1 + STRUCTURAL_RETRIES):
        # M4: topic+seed RE-ROLLED every generate attempt (admin topic stays);
        # the seed that gets logged is the successful round's.
        ts = pick_topic_and_seed(level, rng, admin_topic=section_prompt)
        payload: dict[str, Any] = {
            "prompt_version": version, "core": core,
            "exam_context": scrub_ctx, "spec": spec,
            "topic": ts["topic"], "genre": ts["genre"],
            "diversity_seed": ts["diversity_seed"],
        }
        if last_err:
            payload["retry_error"] = last_err  # numbers/labels only (M3)
        try:
            ai_out = await generator.generate_section(payload, k=k)
            section, justifications = ce["assemble"](source_section, ai_out, preset)
            if preset is not None:
                # Explicit, field-coded preset conformance (clear retry message).
                errs = preset_validator.validate_output_against_preset(section, preset)
                if errs:
                    raise StructureMismatch("; ".join(e.message for e in errs))
            ce["code_checks"](section, spec, src_material, rng, preset)
            section, review = await ce["verify"](
                source_section, section, payload, k, generator, rounds,
                spec, src_material, rng, preset)
            _validate_section_structure(structure_ref, section)
            try:
                overlap = compute_verbatim_overlap(source_section, section)
            except Exception:  # noqa: BLE001 — shadow metric never fails a job
                logger.warning("verbatim-overlap metric failed (ignored)", exc_info=True)
                overlap = {"max": None, "weighted_avg": None, "fields": [],
                           "error": "metric_failed"}
            pct, _common = spec_mode.trigram_overlap(
                (section.get("materials") or [{}])[0].get("content") or "",
                src_material)
            report: dict[str, Any] = {
                "self_review": review,
                "justifications": justifications,
                "prompt_version": version,
                "verbatim_overlap": overlap,
                "mode": "spec",
                "core": core,
                "topic": ts["topic"],
                "diversity_seed": ts["diversity_seed"],
                "skill_map_hash": sm_hash,
                "trigram_overlap_pct": round(pct, 1),
            }
            if preset is not None:
                report["part_code"] = preset.part_code
            return section, report
        except (StructureMismatch, ValidationError) as e:
            last_err = str(e)
    raise SectionGenerationError(last_err or "unknown", review=review)


# ---------------------------------------------------------------------------
# Core — one section through the full pipeline (§2.1, §7, §8, §9.2)
# ---------------------------------------------------------------------------


async def generate_one_section(
    source_section: dict[str, Any],
    k: int,
    *,
    exam_context: dict[str, Any],
    generator,
    type_prompt: Optional[str] = None,
    section_prompt: Optional[str] = None,
    rounds: int = 2,
    prompt_version: Optional[str] = None,
    rng: Optional[_random.Random] = None,
    skill_map_cache_override: Optional[SkillMapCache] = None,
    preset: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Produce one validated section, or raise SectionGenerationError.

    Returns (merged_section, section_report). The section_report has
    `self_review` ({rounds, final_issues}), `justifications`,
    `prompt_version` and the shadow `verbatim_overlap` metric; on
    spec-capable versions it also carries `mode` (+ spec provenance).

    HOST of the thin core assigner (design §2/F1): orchestration gate (K,
    level) + core eligibility decide spec-vs-rewrite; the spec engine below
    RECEIVES the core and never routes.
    """
    version = _validate_prompt_version(prompt_version)
    pv = prompts.get_prompt_version(version)

    adapter_version = version
    mode: Optional[str] = None
    eligibility_reason: Optional[str] = None
    if pv.spec_mode:
        from services.ai import spec_mode
        from services.presets import supports_ai_gen
        # With a preset, the core is the preset's ai_core (validate source against
        # THAT core), not guessed from the source (design §4.1).
        preferred_core = (preset.ai_core
                          if (preset is not None and supports_ai_gen(preset)) else None)
        core, eligibility_reason = spec_mode.assign_core_with_reason(
            source_section, k, (exam_context or {}).get("level"),
            preferred_core=preferred_core)
        if core:
            section, report = await _generate_section_spec(
                source_section, k, core=core, exam_context=exam_context,
                generator=generator, section_prompt=section_prompt,
                rounds=rounds, version=version, rng=rng,
                cache=skill_map_cache_override, preset=preset,
            )
            report["eligibility_reason"] = eligibility_reason  # B6 (FE surface)
            return section, report
        # Rewrite fallback: adapter-level config = v2 (docs §10.4); the
        # service still reports prompt_version=v3 + mode=rewrite.
        adapter_version = REWRITE_FALLBACK_VERSION
        mode = "rewrite"

    payload = prompts.build_section_payload(
        source_section, exam_context,
        type_prompt=type_prompt, section_prompt=section_prompt,
        prompt_version=adapter_version,
    )
    last_err = "unknown"
    review: dict[str, Any] = {"rounds": 0, "final_issues": []}
    for _ in range(1 + STRUCTURAL_RETRIES):
        try:
            ai_out = await generator.generate_section(payload, k=k)
            section, justifications = _merge_generated_section(source_section, ai_out)
            section, review = await _self_review(
                source_section, section, payload, k, generator, rounds
            )
            if any(i.get("severity") == "critical" for i in review["final_issues"]):
                raise StructureMismatch(
                    "self-review left critical issues: "
                    + "; ".join(i.get("problem", "") for i in review["final_issues"]
                                if i.get("severity") == "critical")
                )
            # Post-process by code (all prompt versions): balanced key shuffle
            # AFTER the final (possibly judge-fixed) section, BEFORE Tầng B so
            # validation runs on the exact artifact that gets persisted.
            shuffle_answer_keys(section)
            _validate_section_structure(source_section, section)
            try:
                overlap = compute_verbatim_overlap(source_section, section)
            except Exception:  # noqa: BLE001 — shadow metric must NEVER fail a job
                logger.warning("verbatim-overlap metric failed (ignored)", exc_info=True)
                overlap = {"max": None, "weighted_avg": None, "fields": [],
                           "error": "metric_failed"}
            report: dict[str, Any] = {
                "self_review": review,
                "justifications": justifications,
                "prompt_version": version,
                "verbatim_overlap": overlap,
            }
            if mode:  # spec-capable version that fell back to rewrite
                report["mode"] = mode
            if eligibility_reason:  # B6 — why this section took the rewrite path
                report["eligibility_reason"] = eligibility_reason
            return section, report
        except (StructureMismatch, ValidationError) as e:
            last_err = str(e)
            payload = {**payload, "retry_error": last_err}
    raise SectionGenerationError(last_err, review=review)


async def _self_review(
    source_section: dict[str, Any],
    section: dict[str, Any],
    payload: dict[str, Any],
    k: int,
    generator,
    rounds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tầng A (§7): up to `rounds` judge passes; apply fixed_section each time."""
    if rounds <= 0:
        return section, {"rounds": 0, "final_issues": []}
    issues: list[dict[str, Any]] = []
    done = 0
    for _ in range(rounds):
        verdict = await generator.verify_section(section, payload, k=k)
        done += 1
        issues = verdict.get("issues") or []
        if verdict.get("is_acceptable") and not issues:
            return section, {"rounds": done, "final_issues": []}
        fixed = verdict.get("fixed_section")
        if isinstance(fixed, dict):
            try:
                section, _ = _merge_generated_section(source_section, fixed)
            except StructureMismatch:
                break  # bad fix — stop, surface remaining issues
        else:
            break  # no fix offered — cannot improve
    return section, {"rounds": done, "final_issues": issues}


# ---------------------------------------------------------------------------
# Service — loaders + the three entry points
# ---------------------------------------------------------------------------


class ExamGenerationService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def _load_exam_for_gen(self, exam_id: str) -> dict[str, Any]:
        """Load exam + active sections (materials WITH meta) + active questions
        (question_data WITH answers — NOT stripped, §1). Raises NotFound /
        ValidationError(no active questions)."""
        from services.section_service import _coerce_jsonb
        async with self.db.acquire() as conn:
            exam = await conn.fetchrow(
                "SELECT id, title, level, skill, duration_minutes, description "
                "FROM public.exams WHERE id = $1 AND deleted_at IS NULL", exam_id,
            )
            if not exam:
                raise NotFoundError(f"Exam {exam_id} not found")
            srows = await conn.fetch(
                "SELECT id, position, part_label, type, instructions, materials, "
                "max_audio_plays FROM public.sections "
                "WHERE exam_id = $1 AND deleted_at IS NULL "
                "ORDER BY position ASC, created_at ASC", exam_id,
            )
            sids = [r["id"] for r in srows]
            qrows = await conn.fetch(
                "SELECT id, section_id, position, question_type, question_data, points "
                "FROM public.questions WHERE section_id = ANY($1::uuid[]) "
                "AND deleted_at IS NULL ORDER BY position ASC, created_at ASC",
                sids,
            ) if sids else []

        q_by_section: dict[str, list[dict[str, Any]]] = {}
        for q in qrows:
            q_by_section.setdefault(str(q["section_id"]), []).append({
                "id": str(q["id"]), "position": q["position"],
                "question_type": q["question_type"],
                "question_data": _coerce_jsonb(q["question_data"]),
                "points": q["points"],
            })
        sections = [_normalize_section_positions({
            "id": str(s["id"]), "position": s["position"],
            "part_label": s["part_label"], "type": s["type"],
            "instructions": s["instructions"],
            "materials": _coerce_jsonb(s["materials"]) or [],
            "max_audio_plays": s["max_audio_plays"],
            "questions": q_by_section.get(str(s["id"]), []),
        }) for s in srows]

        if not any(s["questions"] for s in sections):
            raise ValidationError("source exam has no active questions")
        return {
            "id": str(exam["id"]), "title": exam["title"], "level": exam["level"],
            "skill": exam["skill"], "duration_minutes": exam["duration_minutes"],
            "description": exam["description"], "sections": sections,
        }

    async def load_section_for_gen(self, section_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Load one active section (+ its exam context) for Mode 2 single."""
        from services.section_service import _coerce_jsonb
        async with self.db.acquire() as conn:
            s = await conn.fetchrow(
                "SELECT s.id, s.position, s.part_label, s.type, s.instructions, "
                "s.materials, s.max_audio_plays, s.exam_id, "
                "e.level, e.skill, e.title "
                "FROM public.sections s JOIN public.exams e ON e.id = s.exam_id "
                "WHERE s.id = $1 AND s.deleted_at IS NULL AND e.deleted_at IS NULL",
                section_id,
            )
            if not s:
                raise NotFoundError(f"Section {section_id} not found")
            qrows = await conn.fetch(
                "SELECT id, position, question_type, question_data, points "
                "FROM public.questions WHERE section_id = $1 AND deleted_at IS NULL "
                "ORDER BY position ASC, created_at ASC", section_id,
            )
        section = {
            "id": str(s["id"]), "exam_id": str(s["exam_id"]),
            "position": s["position"],
            "part_label": s["part_label"], "type": s["type"],
            "instructions": s["instructions"],
            "materials": _coerce_jsonb(s["materials"]) or [],
            "max_audio_plays": s["max_audio_plays"],
            "questions": [{
                "id": str(q["id"]), "position": q["position"],
                "question_type": q["question_type"],
                "question_data": _coerce_jsonb(q["question_data"]), "points": q["points"],
            } for q in qrows],
        }
        _normalize_section_positions(section)
        exam_context = {"level": s["level"], "skill": s["skill"], "title": s["title"]}
        return section, exam_context

    # ------------------------------------------------------------------
    # Prechecks — run synchronously at POST time so the route returns
    # 404/400 BEFORE a job is created / tokens spent (§14.4).
    # ------------------------------------------------------------------

    async def precheck_exam_source(self, source_exam_id: str) -> None:
        src = await self._load_exam_for_gen(source_exam_id)
        _assert_source_media_meta(src["sections"])

    async def precheck_section_source(self, section_id: str) -> None:
        section, _ = await self.load_section_for_gen(section_id)
        _assert_source_media_meta([section])

    # ------------------------------------------------------------------
    # Mode 1 — whole exam, all-or-nothing, auto-save (§9)
    # ------------------------------------------------------------------

    async def generate_similar_exam(
        self, source_exam_id: str, k: int, *,
        created_by: Optional[str] = None, title: Optional[str] = None,
        section_prompts: Optional[dict[str, str]] = None,
        generator=None, rounds: Optional[int] = None,
        progress_cb: ProgressCb = None, dry_run: bool = False,
        model: Optional[str] = None, provider: Optional[str] = None,
        prompt_version: Optional[str] = None,
    ) -> dict[str, Any]:
        _validate_k(k)
        prompt_version = _validate_prompt_version(prompt_version)
        src = await self._load_exam_for_gen(source_exam_id)
        _assert_source_media_meta(src["sections"])
        gen, rounds = await _resolve_generation(generator, provider, model, rounds)
        type_prompts = await section_type_prompt_service.load_map()
        section_prompts = section_prompts or {}
        exam_context = {"level": src["level"], "skill": src["skill"], "title": src["title"]}

        total = len(src["sections"])
        report: dict[str, Any] = {
            "sections_total": total, "sections_ok": 0, "sections": [],
            "self_review": {}, "media_todos": [], "token_usage": {},
            "section_prompts": section_prompts,
            "prompt_version": prompt_version, "verbatim_overlap": {},
        }
        gen_sections: list[dict[str, Any]] = []
        for idx, sec in enumerate(src["sections"]):
            # new_pos = position this section will have in the SAVED exam
            # (create_exam_nested re-assigns 1..N by array order). Report keys
            # use new_pos so self_review/media_todos all line up with the new
            # exam, even when source positions are non-contiguous.
            new_pos = idx + 1
            if progress_cb:
                await progress_cb(idx, total)
            try:
                gsec, srep = await generate_one_section(
                    sec, k, exam_context=exam_context, generator=gen,
                    type_prompt=type_prompts.get(sec["type"]),
                    section_prompt=section_prompts.get(str(sec["id"])),
                    rounds=rounds, prompt_version=prompt_version,
                )
            except SectionGenerationError as e:
                report["token_usage"] = getattr(gen, "usage", {})
                report["sections"].append({
                    "position": new_pos, "source_position": sec["position"],
                    "status": "failed", "reason": str(e),
                })
                raise GenerationAborted(f"section {new_pos}: {e}", report)
            gen_sections.append(gsec)
            entry = {
                "position": new_pos, "source_position": sec["position"], "status": "ok",
            }
            spec_extras = {key: srep[key] for key in _SPEC_REPORT_KEYS if key in srep}
            entry.update(spec_extras)
            report["sections"].append(entry)
            if spec_extras:
                report.setdefault("spec_provenance", {})[str(new_pos)] = spec_extras
            report["self_review"][str(new_pos)] = srep["self_review"]
            report["verbatim_overlap"][str(new_pos)] = srep["verbatim_overlap"]

        if len(gen_sections) != total:  # defensive (§9.3.3)
            raise GenerationAborted("generated section count mismatch", report)

        report["media_todos"] = _media_todos(gen_sections)
        report["token_usage"] = getattr(gen, "usage", {})
        report["sections_ok"] = total
        if dry_run:
            report["new_exam_id"] = None
            report["dry_run"] = True
            return report
        meta = _build_meta(source_exam_id, k, gen, section_prompts, report,
                           prompt_version=prompt_version)
        result = await exam_service.create_exam_nested(
            title=title or f"{src['title']} (AI K{k})",
            level=src["level"], skill=src["skill"],
            duration_minutes=src["duration_minutes"], description=src["description"],
            created_by=created_by, sections=gen_sections,
            generated_from_exam_id=source_exam_id, generation_meta=meta,
        )
        report["new_exam_id"] = result["id"]
        report["created_counts"] = result.get("created_counts")
        return report

    # ------------------------------------------------------------------
    # Mode 2 — preview (no save) + single part
    # ------------------------------------------------------------------

    async def generate_sections_preview(
        self, source_exam_id: str, k: int, *,
        section_prompts: Optional[dict[str, str]] = None,
        generator=None, rounds: Optional[int] = None,
        progress_cb: ProgressCb = None,
        model: Optional[str] = None, provider: Optional[str] = None,
        prompt_version: Optional[str] = None,
    ) -> dict[str, Any]:
        _validate_k(k)
        prompt_version = _validate_prompt_version(prompt_version)
        src = await self._load_exam_for_gen(source_exam_id)
        _assert_source_media_meta(src["sections"])
        gen, rounds = await _resolve_generation(generator, provider, model, rounds)
        type_prompts = await section_type_prompt_service.load_map()
        section_prompts = section_prompts or {}
        exam_context = {"level": src["level"], "skill": src["skill"], "title": src["title"]}

        total = len(src["sections"])
        out: list[dict[str, Any]] = []
        for idx, sec in enumerate(src["sections"]):
            if progress_cb:
                await progress_cb(idx, total)
            # position = order in the (eventual) assembled exam; FE maps back
            # to the source via source_section_id.
            entry = {"source_section_id": sec["id"], "position": idx + 1,
                     "source_position": sec["position"]}
            try:
                gsec, srep = await generate_one_section(
                    sec, k, exam_context=exam_context, generator=gen,
                    type_prompt=type_prompts.get(sec["type"]),
                    section_prompt=section_prompts.get(str(sec["id"])),
                    rounds=rounds, prompt_version=prompt_version,
                )
                entry.update({"status": "ok", "section": gsec,
                              "self_review": srep["self_review"],
                              "verbatim_overlap": srep["verbatim_overlap"]})
                entry.update({key: srep[key] for key in _SPEC_REPORT_KEYS
                              if key in srep})
            except SectionGenerationError as e:
                entry.update({"status": "failed", "reason": str(e)})  # per-part (§9.6)
            out.append(entry)
        return {
            "sections": out, "sections_total": total,
            "sections_ok": sum(1 for e in out if e["status"] == "ok"),
            "token_usage": getattr(gen, "usage", {}),
            "prompt_version": prompt_version,
        }

    async def generate_one_part(
        self, source_section_id: str, k: int, *,
        section_prompt: Optional[str] = None, generator=None,
        rounds: Optional[int] = None,
        model: Optional[str] = None, provider: Optional[str] = None,
        prompt_version: Optional[str] = None,
        part_code: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mode 2 single part — returns the generated section payload (no save).

        `part_code` (optional): bind this part to a Cambridge preset so the
        generated structure follows the PRESET (counts/options/word-count/CEFR),
        not the source. Unknown code → ValidationError (→ 400)."""
        _validate_k(k)
        prompt_version = _validate_prompt_version(prompt_version)
        from services.presets import resolve_preset, supports_ai_gen
        preset = resolve_preset(part_code)
        if preset is not None and not supports_ai_gen(preset):
            raise ValidationError(
                f"part_code {preset.part_code!r} (core {preset.ai_core!r}) chưa hỗ "
                "trợ AI-gen đợt này. Builder/scaffold vẫn dùng được."
            )
        section, exam_context = await self.load_section_for_gen(source_section_id)
        _assert_source_media_meta([section])
        gen, rounds = await _resolve_generation(generator, provider, model, rounds)
        type_prompts = await section_type_prompt_service.load_map()
        gsec, srep = await generate_one_section(
            section, k, exam_context=exam_context, generator=gen,
            type_prompt=type_prompts.get(section["type"]),
            section_prompt=section_prompt, rounds=rounds,
            prompt_version=prompt_version, preset=preset,
        )
        entry = {
            "source_section_id": section["id"], "position": section["position"],
            "status": "ok", "section": gsec, "self_review": srep["self_review"],
            "verbatim_overlap": srep["verbatim_overlap"],
        }
        entry.update({key: srep[key] for key in _SPEC_REPORT_KEYS if key in srep})
        return {
            "sections": [entry],
            "token_usage": getattr(gen, "usage", {}),
            "prompt_version": prompt_version,
        }

    # ------------------------------------------------------------------
    # Mode 2 — Save assembled draft (§14.5)
    # ------------------------------------------------------------------

    async def assemble_generated_exam(
        self, source_exam_id: str, sections: list[dict[str, Any]], *,
        title: Optional[str] = None, created_by: Optional[str] = None,
        k: Optional[int] = None, section_prompts: Optional[dict[str, str]] = None,
        prompt_version: Optional[str] = None,
    ) -> dict[str, Any]:
        if not sections:
            raise ValidationError("sections must not be empty")
        # Provenance only — assemble never calls AI. None = FE didn't say
        # (parts may even mix versions); do NOT default to v1 here, that
        # would fabricate provenance.
        if prompt_version is not None:
            prompt_version = _validate_prompt_version(prompt_version)
        async with self.db.acquire() as conn:
            src = await conn.fetchrow(
                "SELECT title, level, skill, duration_minutes, description FROM "
                "public.exams WHERE id = $1 AND deleted_at IS NULL", source_exam_id,
            )
            if not src:
                raise NotFoundError(f"Exam {source_exam_id} not found")
            src_section_count = await conn.fetchval(
                "SELECT count(*) FROM public.sections WHERE exam_id = $1 "
                "AND deleted_at IS NULL", source_exam_id,
            )

        meta = {
            "source_exam_id": source_exam_id, "k": k, "via": "assemble",
            "prompt_version": prompt_version,
            "section_prompts": section_prompts or {},
            "media_todos": _media_todos(sections),
        }
        default_title = f"{src['title']} (AI K{k})" if k else f"{src['title']} (AI)"
        # create_exam_nested validates each section (materials/question_data/gap)
        # — bad shapes raise ValidationError (-> 400 at the route).
        result = await exam_service.create_exam_nested(
            title=title or default_title,
            level=src["level"], skill=src["skill"],
            duration_minutes=src["duration_minutes"], description=src["description"],
            created_by=created_by, sections=sections,
            generated_from_exam_id=source_exam_id, generation_meta=meta,
        )
        warning = None
        if len(sections) < (src_section_count or 0):
            warning = (
                f"saved {len(sections)} parts but source has {src_section_count} "
                "active sections"
            )
        return {"exam": result, "warning": warning}


async def _resolve_generation(generator, provider, model, rounds):
    """Resolve the generator + self-review rounds.

    Precedence per field: explicit per-request override > DB
    ai_generation_settings > env default. An injected `generator` (tests) is
    used as-is; only `rounds` is still resolved.
    """
    from services.ai_settings_service import ai_settings_service
    eff = await ai_settings_service.get_effective()
    if generator is None:
        generator = get_ai_generator(
            provider=provider or eff["provider"],
            model=model or eff["model"],
            max_tokens=eff["max_tokens"],
        )
    rounds = rounds if rounds is not None else eff["self_review_rounds"]
    return generator, rounds


def _build_meta(source_exam_id, k, gen, section_prompts, report, *,
                prompt_version: Optional[str] = None) -> dict[str, Any]:
    from config.settings import get_settings
    s = get_settings()
    return {
        "source_exam_id": source_exam_id, "k": k,
        # actual provider/model used (FE override or env default), for provenance
        "provider": getattr(gen, "provider", s.ai_provider),
        "model": getattr(gen, "model", s.ai_model),
        "prompt_version": prompt_version or prompts.DEFAULT_PROMPT_VERSION,
        "section_prompts": section_prompts,
        "media_todos": report.get("media_todos", []),
        "self_review": report.get("self_review", {}),
        # shadow anti-clone metric (per new section position) — audit only
        "verbatim_overlap": report.get("verbatim_overlap", {}),
        # v3 spec provenance (mode/topic/seed/hash/trigram per section).
        # Mode 1 only by design — the assemble path can't carry it (docs
        # exam-gen-v3-spec-mode §11, decision #17).
        "spec_provenance": report.get("spec_provenance", {}),
        "token_usage": getattr(gen, "usage", {}),
    }


exam_generation_service = ExamGenerationService()
