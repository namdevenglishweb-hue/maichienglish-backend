import json
import logging
from typing import Any, Optional

from services.exam_guards import assert_exam_has_no_attempts
from services.exceptions import NotFoundError, ValidationError
from services.question_service import _validate_question_data
from services.section_service import (
    _ALLOWED_TYPES as _ALLOWED_SECTION_TYPES,
    _validate_materials,
    validate_gap_markers,
)

logger = logging.getLogger(__name__)


def _row_to_exam(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "level": row["level"],
        "skill": row["skill"],
        "duration_minutes": row["duration_minutes"],
        "description": row["description"],
        "is_published": row["is_published"],
        "created_by": str(row["created_by"]) if row["created_by"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
        "generated_from_exam_id": (
            str(row["generated_from_exam_id"])
            if row["generated_from_exam_id"] else None
        ),
        "generation_meta": _coerce_jsonb(row["generation_meta"]),
    }


def _coerce_jsonb(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


_SELECT_COLS = """
    id, title, level, skill, duration_minutes, description,
    is_published, created_by, created_at, updated_at, deleted_at,
    generated_from_exam_id, generation_meta
"""


class ExamService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_exam(
        self,
        title: str,
        level: str,
        skill: str,
        duration_minutes: int = 45,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.exams
                    (title, level, skill, duration_minutes, description, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {_SELECT_COLS}
                """,
                title,
                level,
                skill,
                duration_minutes,
                description,
                created_by,
            )
        logger.info("Created exam: %s (level=%s, skill=%s)", row["id"], level, skill)
        return _row_to_exam(row)

    async def get_exam(self, exam_id: str) -> Optional[dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.exams
                WHERE id = $1 AND deleted_at IS NULL
                """,
                exam_id,
            )
        return _row_to_exam(row) if row else None

    async def list_exams(
        self,
        level: Optional[str] = None,
        skill: Optional[str] = None,
        is_published: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if level is not None:
            params.append(level)
            clauses.append(f"level = ${len(params)}")
        if skill is not None:
            params.append(skill)
            clauses.append(f"skill = ${len(params)}")
        if is_published is not None:
            params.append(is_published)
            clauses.append(f"is_published = ${len(params)}")

        where = " AND ".join(clauses)
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.exams
                WHERE {where}
                ORDER BY created_at DESC
                """,
                *params,
            )
        return [_row_to_exam(r) for r in rows]

    async def update_exam(self, exam_id: str, **fields) -> dict[str, Any]:
        if not fields:
            raise ValidationError("No fields to update")

        allowed = {
            "title",
            "level",
            "skill",
            "duration_minutes",
            "description",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValidationError("No updatable fields supplied")

        set_parts = []
        params: list[Any] = []
        for k, v in updates.items():
            params.append(v)
            set_parts.append(f"{k} = ${len(params)}")
        params.append(exam_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.exams
                SET {', '.join(set_parts)}, updated_at = now()
                WHERE id = ${len(params)} AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                *params,
            )
        if not row:
            logger.warning("update_exam: exam %s not found or already deleted", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Updated exam %s (fields=%s)", exam_id, list(updates))
        return _row_to_exam(row)

    async def publish_exam(self, exam_id: str) -> dict[str, Any]:
        """Publish if the exam has at least one active question across all sections."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    "SELECT id FROM public.exams WHERE id = $1 AND deleted_at IS NULL",
                    exam_id,
                )
                if not exam:
                    logger.warning("publish_exam: exam %s not found", exam_id)
                    raise NotFoundError(f"Exam {exam_id} not found")

                count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    WHERE s.exam_id = $1
                      AND s.deleted_at IS NULL
                      AND q.deleted_at IS NULL
                    """,
                    exam_id,
                )
                if count == 0:
                    logger.warning(
                        "publish_exam: exam %s has no active sections/questions", exam_id
                    )
                    raise ValidationError(
                        "Cannot publish exam with no active questions"
                    )

                row = await conn.fetchrow(
                    f"""
                    UPDATE public.exams
                    SET is_published = true, updated_at = now()
                    WHERE id = $1
                    RETURNING {_SELECT_COLS}
                    """,
                    exam_id,
                )
        logger.info("Published exam %s (%d active questions)", exam_id, count)
        return _row_to_exam(row)

    async def unpublish_exam(self, exam_id: str) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            # Publish-lock: an exam that already has attempts is frozen — it
            # cannot be unpublished (and thus cannot have its content edited).
            await assert_exam_has_no_attempts(conn, exam_id)
            row = await conn.fetchrow(
                f"""
                UPDATE public.exams
                SET is_published = false, updated_at = now()
                WHERE id = $1 AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                exam_id,
            )
        if not row:
            logger.warning("unpublish_exam: exam %s not found", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Unpublished exam %s", exam_id)
        return _row_to_exam(row)

    async def soft_delete_exam(self, exam_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE public.exams
                SET deleted_at = now(), updated_at = now(), is_published = false
                WHERE id = $1 AND deleted_at IS NULL
                """,
                exam_id,
            )
        updated = int(result.split()[-1]) if result else 0
        if updated == 0:
            logger.warning("soft_delete_exam: exam %s not found or already deleted", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Soft-deleted exam %s", exam_id)

    async def hard_delete_exam(self, exam_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM public.exams WHERE id = $1",
                exam_id,
            )
        deleted = int(result.split()[-1]) if result else 0
        if deleted == 0:
            logger.warning("hard_delete_exam: exam %s not found", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Hard-deleted exam %s (CASCADE)", exam_id)

    # ------------------------------------------------------------------
    # Nested create — option D from the bulk-endpoint plan
    # ------------------------------------------------------------------

    async def create_exam_nested(
        self,
        title: str,
        level: str,
        skill: str,
        duration_minutes: int = 45,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
        sections: Optional[list[dict[str, Any]]] = None,
        generated_from_exam_id: Optional[str] = None,
        generation_meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create exam + optional sections + optional questions in 1 transaction.

        Positions are server-assigned in array order (sections[0] → position 1,
        questions[0] → position 1 within its section). Admin-provided position
        fields are ignored — reorder the arrays to control ordering.

        Validates per-section gap markers against question positions.

        Args:
            title/level/skill/duration_minutes/description/created_by:
                same as create_exam.
            sections: optional list of section payloads. Each payload may
                contain a `questions` key with question payloads.

        Returns:
            Exam dict with `created_counts: {sections, questions}`.

        Raises:
            ValidationError: any section/question payload invalid, or any
                gap marker references a missing question position.
        """
        sections = sections or []

        # Validate everything BEFORE opening the transaction so we fail fast
        # and don't leave half-written state under any race.
        normalized_sections: list[dict[str, Any]] = []
        for si, sec in enumerate(sections):
            sec_type = sec.get("type")
            if sec_type is not None and sec_type not in _ALLOWED_SECTION_TYPES:
                raise ValidationError(
                    f"sections[{si}]: invalid type {sec_type!r}"
                )
            materials = _validate_materials(sec.get("materials"))

            questions = sec.get("questions") or []
            normalized_qs: list[dict[str, Any]] = []
            question_positions: set[int] = set()
            for qi, q in enumerate(questions):
                qtype = q.get("question_type")
                qdata_raw = q.get("question_data")
                if qtype is None or qdata_raw is None:
                    raise ValidationError(
                        f"sections[{si}].questions[{qi}]: question_type "
                        f"and question_data are required"
                    )
                try:
                    qdata = _validate_question_data(qtype, qdata_raw)
                except ValidationError as e:
                    raise ValidationError(
                        f"sections[{si}].questions[{qi}]: {e}"
                    )
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

            # Gap-marker integrity: every {{gap:N}} must resolve to a question
            validate_gap_markers(
                materials,
                question_positions,
                section_label=f"sections[{si}]",
            )

            normalized_sections.append(
                {
                    "position": si + 1,
                    "part_label": sec.get("part_label") or sec.get("partLabel"),
                    "type": sec_type,
                    "instructions": sec.get("instructions"),
                    "materials": materials,
                    "max_audio_plays": (
                        sec.get("max_audio_plays")
                        if sec.get("max_audio_plays") is not None
                        else sec.get("maxAudioPlays")
                    ),
                    "questions": normalized_qs,
                }
            )

        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam_row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.exams
                        (title, level, skill, duration_minutes, description, created_by,
                         generated_from_exam_id, generation_meta)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    RETURNING {_SELECT_COLS}
                    """,
                    title, level, skill, duration_minutes, description, created_by,
                    generated_from_exam_id,
                    json.dumps(generation_meta) if generation_meta is not None else None,
                )
                exam_id = exam_row["id"]
                created_sections = 0
                created_questions = 0
                for sec in normalized_sections:
                    section_row = await conn.fetchrow(
                        """
                        INSERT INTO public.sections
                            (exam_id, position, part_label, type, instructions,
                             materials, max_audio_plays)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                        RETURNING id
                        """,
                        exam_id,
                        sec["position"],
                        sec["part_label"],
                        sec["type"],
                        sec["instructions"],
                        json.dumps(sec["materials"]),
                        sec["max_audio_plays"],
                    )
                    created_sections += 1
                    section_id = section_row["id"]
                    for q in sec["questions"]:
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
                        created_questions += 1

        logger.info(
            "Created exam %s nested (%d sections, %d questions)",
            exam_id, created_sections, created_questions,
        )
        result = _row_to_exam(exam_row)
        result["created_counts"] = {
            "sections": created_sections,
            "questions": created_questions,
        }
        return result


exam_service = ExamService()
