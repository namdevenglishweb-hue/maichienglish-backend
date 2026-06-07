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
    source: dict[str, Any], ai_out: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge AI content onto the source, re-imposing all hard invariants.

    Returns (merged_section, justifications). Raises StructureMismatch when the
    AI returned the wrong number of materials/questions (cannot merge by index).
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
            m: dict[str, Any] = {
                "type": "text",
                "content": am.get("content") or sm.get("content") or "",
            }
            label = am.get("label", sm.get("label"))
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

    src_qs = source.get("questions") or []
    ai_qs = ai_out.get("questions") or []
    if len(ai_qs) != len(src_qs):
        raise StructureMismatch(
            f"expected {len(src_qs)} questions, got {len(ai_qs)}"
        )
    out_qs: list[dict[str, Any]] = []
    justifications: list[dict[str, Any]] = []
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

    merged = {
        "type": source.get("type"),
        "part_label": ai_out.get("part_label") or source.get("part_label"),
        "instructions": ai_out.get("instructions") or source.get("instructions"),
        "max_audio_plays": source.get("max_audio_plays"),
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
        for mi, m in enumerate(s.get("materials") or []):
            if isinstance(m, dict) and (m.get("meta") or {}).get("pendingReplacement"):
                todos.append({
                    "section_position": si + 1,
                    "material_index": mi,
                    "media_type": m.get("type"),
                })
    return todos


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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Produce one validated section, or raise SectionGenerationError.

    Returns (merged_section, section_report). The section_report has
    `self_review` ({rounds, final_issues}) and `justifications`.
    """
    payload = prompts.build_section_payload(
        source_section, exam_context,
        type_prompt=type_prompt, section_prompt=section_prompt,
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
            _validate_section_structure(source_section, section)
            return section, {"self_review": review, "justifications": justifications}
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
    ) -> dict[str, Any]:
        _validate_k(k)
        src = await self._load_exam_for_gen(source_exam_id)
        _assert_source_media_meta(src["sections"])
        gen = generator or get_ai_generator()
        rounds = _resolve_rounds(rounds)
        type_prompts = await section_type_prompt_service.load_map()
        section_prompts = section_prompts or {}
        exam_context = {"level": src["level"], "skill": src["skill"], "title": src["title"]}

        total = len(src["sections"])
        report: dict[str, Any] = {
            "sections_total": total, "sections_ok": 0, "sections": [],
            "self_review": {}, "media_todos": [], "token_usage": {},
            "section_prompts": section_prompts,
        }
        gen_sections: list[dict[str, Any]] = []
        for idx, sec in enumerate(src["sections"]):
            if progress_cb:
                await progress_cb(idx, total)
            try:
                gsec, srep = await generate_one_section(
                    sec, k, exam_context=exam_context, generator=gen,
                    type_prompt=type_prompts.get(sec["type"]),
                    section_prompt=section_prompts.get(str(sec["id"])),
                    rounds=rounds,
                )
            except SectionGenerationError as e:
                report["token_usage"] = getattr(gen, "usage", {})
                report["sections"].append(
                    {"position": sec["position"], "status": "failed", "reason": str(e)}
                )
                raise GenerationAborted(f"section {sec['position']}: {e}", report)
            gen_sections.append(gsec)
            report["sections"].append({"position": sec["position"], "status": "ok"})
            report["self_review"][str(sec["position"])] = srep["self_review"]

        if len(gen_sections) != total:  # defensive (§9.3.3)
            raise GenerationAborted("generated section count mismatch", report)

        report["media_todos"] = _media_todos(gen_sections)
        report["token_usage"] = getattr(gen, "usage", {})
        report["sections_ok"] = total
        if dry_run:
            report["new_exam_id"] = None
            report["dry_run"] = True
            return report
        meta = _build_meta(source_exam_id, k, gen, section_prompts, report)
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
    ) -> dict[str, Any]:
        _validate_k(k)
        src = await self._load_exam_for_gen(source_exam_id)
        _assert_source_media_meta(src["sections"])
        gen = generator or get_ai_generator()
        rounds = _resolve_rounds(rounds)
        type_prompts = await section_type_prompt_service.load_map()
        section_prompts = section_prompts or {}
        exam_context = {"level": src["level"], "skill": src["skill"], "title": src["title"]}

        total = len(src["sections"])
        out: list[dict[str, Any]] = []
        for idx, sec in enumerate(src["sections"]):
            if progress_cb:
                await progress_cb(idx, total)
            entry = {"source_section_id": sec["id"], "position": sec["position"]}
            try:
                gsec, srep = await generate_one_section(
                    sec, k, exam_context=exam_context, generator=gen,
                    type_prompt=type_prompts.get(sec["type"]),
                    section_prompt=section_prompts.get(str(sec["id"])),
                    rounds=rounds,
                )
                entry.update({"status": "ok", "section": gsec,
                              "self_review": srep["self_review"]})
            except SectionGenerationError as e:
                entry.update({"status": "failed", "reason": str(e)})  # per-part (§9.6)
            out.append(entry)
        return {
            "sections": out, "sections_total": total,
            "sections_ok": sum(1 for e in out if e["status"] == "ok"),
            "token_usage": getattr(gen, "usage", {}),
        }

    async def generate_one_part(
        self, source_section_id: str, k: int, *,
        section_prompt: Optional[str] = None, generator=None,
        rounds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Mode 2 single part — returns the generated section payload (no save)."""
        _validate_k(k)
        section, exam_context = await self.load_section_for_gen(source_section_id)
        _assert_source_media_meta([section])
        gen = generator or get_ai_generator()
        rounds = _resolve_rounds(rounds)
        type_prompts = await section_type_prompt_service.load_map()
        gsec, srep = await generate_one_section(
            section, k, exam_context=exam_context, generator=gen,
            type_prompt=type_prompts.get(section["type"]),
            section_prompt=section_prompt, rounds=rounds,
        )
        return {
            "sections": [{
                "source_section_id": section["id"], "position": section["position"],
                "status": "ok", "section": gsec, "self_review": srep["self_review"],
            }],
            "token_usage": getattr(gen, "usage", {}),
        }

    # ------------------------------------------------------------------
    # Mode 2 — Save assembled draft (§14.5)
    # ------------------------------------------------------------------

    async def assemble_generated_exam(
        self, source_exam_id: str, sections: list[dict[str, Any]], *,
        title: Optional[str] = None, created_by: Optional[str] = None,
        k: Optional[int] = None, section_prompts: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        if not sections:
            raise ValidationError("sections must not be empty")
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


def _resolve_rounds(rounds: Optional[int]) -> int:
    if rounds is not None:
        return rounds
    from config.settings import get_settings
    return get_settings().ai_self_review_rounds


def _build_meta(source_exam_id, k, gen, section_prompts, report) -> dict[str, Any]:
    from config.settings import get_settings
    s = get_settings()
    return {
        "source_exam_id": source_exam_id, "k": k,
        "provider": s.ai_provider, "model": s.ai_model,
        "section_prompts": section_prompts,
        "media_todos": report.get("media_todos", []),
        "self_review": report.get("self_review", {}),
        "token_usage": getattr(gen, "usage", {}),
    }


exam_generation_service = ExamGenerationService()
