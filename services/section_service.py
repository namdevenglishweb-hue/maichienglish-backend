"""Section CRUD — middle layer of Exam → Section → Question.

Owns passage materials (JSONB), optional listening audio + replay cap, and
the instruction rubric. Per-section position is the `N` referenced by
`{{gap:N}}` markers inside materials content (see plan §3.5).
"""
import json
import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from services.exam_guards import (
    assert_exam_content_editable,
    assert_section_editable,
    assert_sections_editable,
)
from services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

GAP_MARKER_RE = re.compile(r"\{\{gap:(\d+)\}\}")


def validate_gap_markers(
    materials: list[dict[str, Any]],
    question_positions: set[int],
    section_label: str = "section",
) -> None:
    """Raise ValidationError if any `{{gap:N}}` marker in materials references
    a question position that doesn't exist in `question_positions`.

    Used by nested-create endpoints to catch broken passages at import time.
    Not enforced on granular section/question CRUD (admin may edit in any
    order — see plan §3.5).
    """
    for i, m in enumerate(materials):
        if not isinstance(m, dict) or m.get("type") != "text":
            continue
        content = m.get("content") or ""
        for match in GAP_MARKER_RE.finditer(content):
            n = int(match.group(1))
            if n not in question_positions:
                raise ValidationError(
                    f"{section_label}: materials[{i}] has marker {{{{gap:{n}}}}} "
                    f"but no question with position={n} exists "
                    f"(known positions: {sorted(question_positions)})"
                )


# ---------------------------------------------------------------------------
# Materials validator — list of typed blocks shown above the questions.
# Three variants discriminated by `type`:
#   - text:  {type, label?, content}                  (passage; supports {{gap:N}})
#   - image: {type, label?, url, alt?}                (diagram, form, illustration)
#   - audio: {type, label?, url}                      (listening clip)
# Audio cap value is section-wide (sections.max_audio_plays); per-audio
# play counters live in attempt_section_state.audio_play_counts.
# ---------------------------------------------------------------------------


# Per-material admin-only meta (docs/exam-ai-generation §5). `transcript`
# (audio) / `description` (image) are the AI's raw input + generated output;
# `pendingReplacement` flags media whose file is still the source's. ALL of
# `meta` is stripped from student-facing payloads (strip_material_meta).
class _AudioMeta(BaseModel):
    transcript: Optional[str] = None
    pendingReplacement: bool = False


class _ImageMeta(BaseModel):
    description: Optional[str] = None
    pendingReplacement: bool = False


class _TextMaterial(BaseModel):
    type: Literal["text"]
    label: Optional[str] = None
    content: str = Field(..., min_length=1)


class _ImageMaterial(BaseModel):
    type: Literal["image"]
    label: Optional[str] = None
    url: str = Field(..., min_length=1)
    alt: Optional[str] = None
    meta: Optional[_ImageMeta] = None


class _AudioMaterial(BaseModel):
    type: Literal["audio"]
    label: Optional[str] = None
    url: str = Field(..., min_length=1)
    meta: Optional[_AudioMeta] = None


_MATERIAL_CLASSES = {
    "text": _TextMaterial,
    "image": _ImageMaterial,
    "audio": _AudioMaterial,
}


def _validate_materials(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError("materials must be a list")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationError(f"materials[{i}]: must be an object")
        mtype = item.get("type")
        cls = _MATERIAL_CLASSES.get(mtype)
        if cls is None:
            raise ValidationError(
                f"materials[{i}]: invalid type {mtype!r}; "
                f"allowed: {sorted(_MATERIAL_CLASSES)}"
            )
        try:
            out.append(cls(**item).model_dump(exclude_none=True))
        except PydanticValidationError as e:
            raise ValidationError(f"materials[{i}]: {e.errors()}")
    return out


def _coerce_jsonb(raw):
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _row_to_section(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "exam_id": str(row["exam_id"]),
        "position": row["position"],
        "part_label": row["part_label"],
        "type": row["type"],
        "instructions": row["instructions"],
        "materials": _coerce_jsonb(row["materials"]) or [],
        "max_audio_plays": row["max_audio_plays"],
        "part_code": row["part_code"],   # Part preset id; NULL = custom (mig 0024)
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
    }


_SELECT_COLS = """
    id, exam_id, position, part_label, type, instructions, materials,
    max_audio_plays, part_code, created_at, updated_at, deleted_at
"""

_ALLOWED_TYPES = {
    "multiple_choice", "fill_blank", "matching", "multiple_choice_shared",
    "writing", "speaking",   # opened in migration 0011; see WRITING_SPEAKING.md
    "form_completion",       # opened in migration 0014; KET note/form completion
                             # (questions stay question_type='fill_blank')
}


class SectionService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_section(
        self,
        exam_id: str,
        part_label: Optional[str] = None,
        type: Optional[str] = None,
        instructions: Optional[str] = None,
        materials: Optional[list[dict]] = None,
        max_audio_plays: Optional[int] = None,
        position: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create a new section under an exam.

        Args:
            exam_id: parent exam (must exist and not be soft-deleted).
            part_label: display label, e.g. "Part 1".
            type: rendering hint — one of multiple_choice/fill_blank/matching, or None.
            instructions: rubric shown to the student.
            materials: list of typed blocks (`text` / `image` / `audio`),
                validated server-side per type.
            max_audio_plays: section-wide cap value applied independently to
                every audio material in this section. Null = unlimited.
            position: 1-based order in the exam. If None, append after MAX.

        Returns:
            Created section dict (camel-free snake_case).

        Raises:
            NotFoundError: parent exam doesn't exist.
            ValidationError: materials or type invalid.
        """
        validated_materials = _validate_materials(materials)
        if type is not None and type not in _ALLOWED_TYPES:
            raise ValidationError(
                f"Invalid section type {type!r}; allowed: {sorted(_ALLOWED_TYPES)}"
            )

        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    "SELECT id FROM public.exams WHERE id = $1 AND deleted_at IS NULL",
                    exam_id,
                )
                if not exam:
                    raise NotFoundError(f"Exam {exam_id} not found")
                await assert_exam_content_editable(conn, exam_id)  # publish-lock

                if position is None:
                    max_pos = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(position), 0)
                        FROM public.sections
                        WHERE exam_id = $1 AND deleted_at IS NULL
                        """,
                        exam_id,
                    )
                    position = max_pos + 1

                row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.sections
                        (exam_id, position, part_label, type, instructions,
                         materials, max_audio_plays)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    RETURNING {_SELECT_COLS}
                    """,
                    exam_id,
                    position,
                    part_label,
                    type,
                    instructions,
                    json.dumps(validated_materials),
                    max_audio_plays,
                )

        logger.info(
            "Created section %s (exam=%s, position=%d, type=%s)",
            row["id"], exam_id, position, type,
        )
        return _row_to_section(row)

    async def list_sections_by_exam(
        self, exam_id: str
    ) -> list[dict[str, Any]]:
        """Return active (non-deleted) sections of an exam ordered by position."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.sections
                WHERE exam_id = $1 AND deleted_at IS NULL
                ORDER BY position ASC, created_at ASC
                """,
                exam_id,
            )
        return [_row_to_section(r) for r in rows]

    async def get_section(self, section_id: str) -> Optional[dict[str, Any]]:
        """Fetch a section by id. Returns None if it doesn't exist or is soft-deleted."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.sections
                WHERE id = $1 AND deleted_at IS NULL
                """,
                section_id,
            )
        return _row_to_section(row) if row else None

    async def update_section(self, section_id: str, **fields) -> dict[str, Any]:
        """Patch a section. Only whitelisted columns are accepted.

        Args:
            section_id: target row.
            **fields: subset of `part_label`, `instructions`, `materials`,
                `audio_url`, `max_audio_plays`, `position`.

        Raises:
            NotFoundError: section doesn't exist or is soft-deleted.
            ValidationError: no updatable fields supplied, or materials invalid.
        """
        if not fields:
            raise ValidationError("No fields to update")

        allowed = {
            "part_label",
            "type",
            "instructions",
            "materials",
            "max_audio_plays",
            "position",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValidationError("No updatable fields supplied")

        if "materials" in updates:
            updates["materials"] = _validate_materials(updates["materials"])
        if "type" in updates and updates["type"] is not None and updates["type"] not in _ALLOWED_TYPES:
            raise ValidationError(
                f"Invalid section type {updates['type']!r}; allowed: {sorted(_ALLOWED_TYPES)}"
            )

        set_parts = []
        params: list[Any] = []
        for k, v in updates.items():
            params.append(json.dumps(v) if k == "materials" else v)
            cast = "::jsonb" if k == "materials" else ""
            set_parts.append(f"{k} = ${len(params)}{cast}")
        params.append(section_id)

        async with self.db.acquire() as conn:
            await assert_section_editable(conn, section_id)  # publish-lock
            row = await conn.fetchrow(
                f"""
                UPDATE public.sections
                SET {', '.join(set_parts)}, updated_at = now()
                WHERE id = ${len(params)} AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                *params,
            )
        if not row:
            raise NotFoundError(f"Section {section_id} not found")
        logger.info("Updated section %s (fields=%s)", section_id, list(updates))
        return _row_to_section(row)

    async def soft_delete_section(self, section_id: str) -> None:
        """Mark a section as deleted (sets `deleted_at = now()`). Questions
        remain in the DB but become unreachable through the published tree.

        Raises:
            NotFoundError: section doesn't exist or is already deleted.
        """
        async with self.db.acquire() as conn:
            await assert_section_editable(conn, section_id)  # publish-lock
            result = await conn.execute(
                """
                UPDATE public.sections
                SET deleted_at = now(), updated_at = now()
                WHERE id = $1 AND deleted_at IS NULL
                """,
                section_id,
            )
        updated = int(result.split()[-1]) if result else 0
        if updated == 0:
            raise NotFoundError(f"Section {section_id} not found")
        logger.info("Soft-deleted section %s", section_id)

    async def hard_delete_section(self, section_id: str) -> None:
        """Hard-delete a section row. `CASCADE`s through questions, answers,
        and `attempt_section_state`. Use only for broken/erroneous content.

        Raises:
            NotFoundError: section doesn't exist.
        """
        async with self.db.acquire() as conn:
            await assert_section_editable(conn, section_id)  # publish-lock
            result = await conn.execute(
                "DELETE FROM public.sections WHERE id = $1",
                section_id,
            )
        deleted = int(result.split()[-1]) if result else 0
        if deleted == 0:
            raise NotFoundError(f"Section {section_id} not found")
        logger.info("Hard-deleted section %s (CASCADE)", section_id)

    # ------------------------------------------------------------------
    # Nested create — section + optional inline questions in one transaction
    # ------------------------------------------------------------------

    async def create_section_with_questions(
        self,
        exam_id: str,
        part_label: Optional[str] = None,
        type: Optional[str] = None,
        instructions: Optional[str] = None,
        materials: Optional[list[dict]] = None,
        max_audio_plays: Optional[int] = None,
        position: Optional[int] = None,
        questions: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Create a section + inline child questions in one transaction.

        Questions are server-assigned positions 1..N in array order.
        Gap markers in materials are validated against the resulting
        question positions before any INSERT runs.

        Returns:
            Section dict + `created_counts: {questions: N}`.
        """
        # Lazy import to avoid circular dep (question_service → section_service helpers)
        from services.question_service import _validate_question_data

        questions = questions or []
        validated_materials = _validate_materials(materials)
        if type is not None and type not in _ALLOWED_TYPES:
            raise ValidationError(
                f"Invalid section type {type!r}; allowed: {sorted(_ALLOWED_TYPES)}"
            )

        # Validate every question payload + gap markers BEFORE opening txn
        normalized_qs: list[dict[str, Any]] = []
        question_positions: set[int] = set()
        for qi, q in enumerate(questions):
            qtype = q.get("question_type")
            qdata_raw = q.get("question_data")
            if qtype is None or qdata_raw is None:
                raise ValidationError(
                    f"questions[{qi}]: question_type and question_data are required"
                )
            try:
                qdata = _validate_question_data(qtype, qdata_raw)
            except ValidationError as e:
                raise ValidationError(f"questions[{qi}]: {e}")
            pos = qi + 1
            question_positions.add(pos)
            normalized_qs.append(
                {
                    "position": pos,
                    "question_type": qtype,
                    "question_data": qdata,
                    "points": q.get("points", 1),
                }
            )
        validate_gap_markers(
            validated_materials, question_positions, section_label="section"
        )

        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    "SELECT id FROM public.exams WHERE id = $1 AND deleted_at IS NULL",
                    exam_id,
                )
                if not exam:
                    raise NotFoundError(f"Exam {exam_id} not found")
                await assert_exam_content_editable(conn, exam_id)  # publish-lock

                if position is None:
                    max_pos = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(position), 0)
                        FROM public.sections
                        WHERE exam_id = $1 AND deleted_at IS NULL
                        """,
                        exam_id,
                    )
                    position = max_pos + 1

                section_row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.sections
                        (exam_id, position, part_label, type, instructions,
                         materials, max_audio_plays)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    RETURNING {_SELECT_COLS}
                    """,
                    exam_id,
                    position,
                    part_label,
                    type,
                    instructions,
                    json.dumps(validated_materials),
                    max_audio_plays,
                )
                section_id = section_row["id"]
                for q in normalized_qs:
                    await conn.execute(
                        """
                        INSERT INTO public.questions
                            (section_id, position, question_type, question_data, points)
                        VALUES ($1, $2, $3, $4::jsonb, $5)
                        """,
                        section_id,
                        q["position"],
                        q["question_type"],
                        json.dumps(q["question_data"]),
                        q["points"],
                    )

        logger.info(
            "Created section %s nested (exam=%s, position=%d, type=%s, %d questions)",
            section_id, exam_id, position, type, len(normalized_qs),
        )
        result = _row_to_section(section_row)
        result["created_counts"] = {"questions": len(normalized_qs)}
        return result

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_update_sections(
        self, updates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Apply N section patches in one transaction; all-or-nothing.

        Each update item must include `id`; remaining fields are patches
        (same whitelist as update_section).

        Raises:
            ValidationError: any item missing id, no fields, or invalid type/materials.
            NotFoundError: any id doesn't exist (entire batch rolled back).
        """
        if not updates:
            raise ValidationError("Empty batch")

        # Pre-validate every patch shape before opening a transaction
        normalized: list[tuple[str, dict[str, Any]]] = []
        allowed = {
            "part_label", "type", "instructions", "materials",
            "max_audio_plays", "position",
        }
        for i, item in enumerate(updates):
            sid = item.get("id")
            if not sid:
                raise ValidationError(f"updates[{i}]: missing `id`")
            patch = {k: v for k, v in item.items() if k != "id" and k in allowed}
            if not patch:
                raise ValidationError(f"updates[{i}]: no updatable fields")
            if "materials" in patch:
                patch["materials"] = _validate_materials(patch["materials"])
            if (
                "type" in patch
                and patch["type"] is not None
                and patch["type"] not in _ALLOWED_TYPES
            ):
                raise ValidationError(
                    f"updates[{i}]: invalid section type {patch['type']!r}"
                )
            normalized.append((sid, patch))

        out: list[dict[str, Any]] = []
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await assert_sections_editable(  # publish-lock
                    conn, [sid for sid, _ in normalized]
                )
                for sid, patch in normalized:
                    set_parts: list[str] = []
                    params: list[Any] = []
                    for k, v in patch.items():
                        params.append(json.dumps(v) if k == "materials" else v)
                        cast = "::jsonb" if k == "materials" else ""
                        set_parts.append(f"{k} = ${len(params)}{cast}")
                    params.append(sid)
                    row = await conn.fetchrow(
                        f"""
                        UPDATE public.sections
                        SET {', '.join(set_parts)}, updated_at = now()
                        WHERE id = ${len(params)} AND deleted_at IS NULL
                        RETURNING {_SELECT_COLS}
                        """,
                        *params,
                    )
                    if not row:
                        raise NotFoundError(f"Section {sid} not found")
                    out.append(_row_to_section(row))
        logger.info("Bulk-updated %d sections", len(out))
        return out

    async def bulk_delete_sections(
        self, ids: list[str], hard: bool = False
    ) -> None:
        """Delete N sections in one transaction. Soft by default; hard CASCADEs.

        Raises:
            ValidationError: empty list.
            NotFoundError: any id missing (entire batch rolled back).
        """
        if not ids:
            raise ValidationError("Empty batch")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                await assert_sections_editable(conn, ids)  # publish-lock
                if hard:
                    result = await conn.execute(
                        "DELETE FROM public.sections WHERE id = ANY($1::uuid[])",
                        ids,
                    )
                else:
                    result = await conn.execute(
                        """
                        UPDATE public.sections
                        SET deleted_at = now(), updated_at = now()
                        WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL
                        """,
                        ids,
                    )
                affected = int(result.split()[-1]) if result else 0
                if affected != len(ids):
                    raise NotFoundError(
                        f"Some section IDs were not found "
                        f"({affected}/{len(ids)} matched)"
                    )
        logger.info(
            "Bulk-%s-deleted %d sections", "hard" if hard else "soft", len(ids),
        )


section_service = SectionService()
