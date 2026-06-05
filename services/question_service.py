import json
import logging
from typing import Any, Optional

from pydantic import (
    BaseModel,
    Field,
    ValidationError as PydanticValidationError,
    model_validator,
)

from services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-type question_data validators. Source of truth: plan §3.6.
# ---------------------------------------------------------------------------


class _MCOption(BaseModel):
    """A single multiple_choice option.

    Each option must have at least one of `text` or `image_url`. Picture MC
    (Listening Part 1) uses image_url; regular MC uses text.
    """

    text: Optional[str] = None
    image_url: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.text and not self.image_url:
            raise ValueError("option must have at least one of `text` or `image_url`")
        return self


class _MultipleChoiceData(BaseModel):
    stem: Optional[str] = None
    options: list[_MCOption] = Field(..., min_length=2)
    correct_index: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _check_index(self):
        if self.correct_index >= len(self.options):
            raise ValueError(
                f"correct_index {self.correct_index} out of range for {len(self.options)} options"
            )
        return self


class _FillBlankData(BaseModel):
    correct_answers: list[str] = Field(..., min_length=1)
    case_sensitive: bool = False
    # Presentation-only fields for `form_completion` sections (KET-style
    # note/form completion — migration 0014). They carry the visible context
    # of each blank and are NOT answers, so strip_correct keeps them:
    #   label   — left-column text, e.g. "Teacher's name:"
    #   prefix  — text immediately before the blank, e.g. "Mr" / "from"
    #   postfix — text immediately after the blank, e.g. "to 5 p.m."
    # Plain passage fill_blank questions simply omit them (exclude_none drops
    # them on the way out). Grading is unchanged — see utils/grading_utils.py.
    label: Optional[str] = None
    prefix: Optional[str] = None
    postfix: Optional[str] = None


# `matching` reuses the MC shape: each matching question is one independently-
# scored row of a shared-options table (KET Listening P5, Reading P2 etc.).
# The rendering distinction is signaled by `section.type` (plan §3.5/§3.6).
#
# `writing` and `speaking` use permissive validators — only `prompt` is
# required; `extra="allow"` keeps optional richer fields (minWords/maxWords,
# exampleAnswer, promptAudioUrl, etc. per docs/writing-speaking/) without
# dropping them. submit_attempt does NOT auto-grade these types: it stores
# the answer with is_correct=NULL / points_earned=0 and a teacher scores
# them later via the manual-grading flow (docs/teacher-grading/).
class _WritingData(BaseModel):
    model_config = {"extra": "allow"}        # tolerate minWords/maxWords/etc.
    prompt: str = Field(..., min_length=1)


class _SpeakingData(BaseModel):
    model_config = {"extra": "allow"}        # tolerate maxDurationSeconds/etc.
    prompt: str = Field(..., min_length=1)


_VALIDATORS = {
    "multiple_choice": _MultipleChoiceData,
    "fill_blank": _FillBlankData,
    "matching": _MultipleChoiceData,
    "writing": _WritingData,
    "speaking": _SpeakingData,
}


def _validate_question_data(question_type: str, data: dict) -> dict:
    cls = _VALIDATORS.get(question_type)
    if cls is None:
        raise ValidationError(f"Unknown question_type '{question_type}'")
    try:
        return cls(**data).model_dump(exclude_none=True)
    except PydanticValidationError as e:
        raise ValidationError(
            f"Invalid question_data for {question_type}: {e.errors()}"
        )


def _coerce_question_data(raw) -> dict:
    """asyncpg returns jsonb as a str — decode if needed."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _row_to_question(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "section_id": str(row["section_id"]),
        "position": row["position"],
        "question_type": row["question_type"],
        "question_data": _coerce_question_data(row["question_data"]),
        "points": row["points"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
    }


_SELECT_COLS = "id, section_id, position, question_type, question_data, points, created_at, deleted_at"


class QuestionService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_question(
        self,
        section_id: str,
        question_type: str,
        question_data: dict,
        points: int = 1,
        position: Optional[int] = None,
    ) -> dict[str, Any]:
        validated_data = _validate_question_data(question_type, question_data)

        async with self.db.acquire() as conn:
            async with conn.transaction():
                section = await conn.fetchrow(
                    "SELECT id FROM public.sections WHERE id = $1 AND deleted_at IS NULL",
                    section_id,
                )
                if not section:
                    logger.warning("create_question: section %s not found", section_id)
                    raise NotFoundError(f"Section {section_id} not found")

                if position is None:
                    max_pos = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(position), 0)
                        FROM public.questions
                        WHERE section_id = $1 AND deleted_at IS NULL
                        """,
                        section_id,
                    )
                    position = max_pos + 1

                row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.questions
                        (section_id, position, question_type, question_data, points)
                    VALUES ($1, $2, $3, $4::jsonb, $5)
                    RETURNING {_SELECT_COLS}
                    """,
                    section_id,
                    position,
                    question_type,
                    json.dumps(validated_data),
                    points,
                )
        logger.info(
            "Created question %s (section=%s, type=%s, position=%d)",
            row["id"], section_id, question_type, position,
        )
        return _row_to_question(row)

    async def list_questions_by_section(
        self, section_id: str
    ) -> list[dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.questions
                WHERE section_id = $1 AND deleted_at IS NULL
                ORDER BY position ASC, created_at ASC
                """,
                section_id,
            )
        return [_row_to_question(r) for r in rows]

    async def get_question(self, question_id: str) -> Optional[dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.questions
                WHERE id = $1 AND deleted_at IS NULL
                """,
                question_id,
            )
        return _row_to_question(row) if row else None

    async def get_exam_id_for_question(self, question_id: str) -> Optional[str]:
        """Resolve the owning exam_id by walking question → section → exam.

        Used by api/questions/routes.py to enforce published-only visibility
        without forcing callers to know the section layer exists.
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT s.exam_id
                FROM public.questions q
                JOIN public.sections s ON s.id = q.section_id
                WHERE q.id = $1 AND q.deleted_at IS NULL
                """,
                question_id,
            )
        return str(row["exam_id"]) if row else None

    async def update_question(self, question_id: str, **fields) -> dict[str, Any]:
        if not fields:
            raise ValidationError("No fields to update")

        current = await self.get_question(question_id)
        if not current:
            logger.warning("update_question: question %s not found", question_id)
            raise NotFoundError(f"Question {question_id} not found")

        new_type = fields.get("question_type", current["question_type"])
        if "question_data" in fields:
            fields["question_data"] = _validate_question_data(
                new_type, fields["question_data"]
            )
        elif "question_type" in fields and new_type != current["question_type"]:
            raise ValidationError(
                "Changing question_type requires also supplying matching question_data"
            )

        allowed = {"question_type", "question_data", "points", "position"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValidationError("No updatable fields supplied")

        set_parts = []
        params: list[Any] = []
        for k, v in updates.items():
            params.append(json.dumps(v) if k == "question_data" else v)
            cast = "::jsonb" if k == "question_data" else ""
            set_parts.append(f"{k} = ${len(params)}{cast}")
        params.append(question_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.questions
                SET {', '.join(set_parts)}
                WHERE id = ${len(params)} AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                *params,
            )
        if not row:
            raise NotFoundError(f"Question {question_id} not found")
        logger.info("Updated question %s (fields=%s)", question_id, list(updates))
        return _row_to_question(row)

    async def soft_delete_question(self, question_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE public.questions
                SET deleted_at = now()
                WHERE id = $1 AND deleted_at IS NULL
                """,
                question_id,
            )
        updated = int(result.split()[-1]) if result else 0
        if updated == 0:
            logger.warning("soft_delete_question: question %s not found", question_id)
            raise NotFoundError(f"Question {question_id} not found")
        logger.info("Soft-deleted question %s", question_id)

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_update_questions(
        self, updates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Apply N question patches in one transaction; all-or-nothing.

        Each item must include `id`. Patches use the same whitelist as
        update_question. Type change still requires matching question_data.

        Raises:
            ValidationError: missing id, no fields, invalid type/data.
            NotFoundError: any id missing (entire batch rolled back).
        """
        if not updates:
            raise ValidationError("Empty batch")

        out: list[dict[str, Any]] = []
        async with self.db.acquire() as conn:
            async with conn.transaction():
                for i, item in enumerate(updates):
                    qid = item.get("id")
                    if not qid:
                        raise ValidationError(f"updates[{i}]: missing `id`")
                    patch = {k: v for k, v in item.items() if k != "id"}
                    if not patch:
                        raise ValidationError(f"updates[{i}]: no updatable fields")

                    current = await conn.fetchrow(
                        f"SELECT {_SELECT_COLS} FROM public.questions "
                        "WHERE id = $1 AND deleted_at IS NULL",
                        qid,
                    )
                    if not current:
                        raise NotFoundError(f"Question {qid} not found")

                    new_type = patch.get("question_type", current["question_type"])
                    if "question_data" in patch:
                        patch["question_data"] = _validate_question_data(
                            new_type, patch["question_data"]
                        )
                    elif (
                        "question_type" in patch
                        and new_type != current["question_type"]
                    ):
                        raise ValidationError(
                            f"updates[{i}]: changing question_type requires "
                            f"matching question_data"
                        )

                    allowed = {
                        "question_type", "question_data", "points", "position",
                    }
                    patch = {k: v for k, v in patch.items() if k in allowed}
                    if not patch:
                        raise ValidationError(
                            f"updates[{i}]: no updatable fields after whitelist"
                        )

                    set_parts: list[str] = []
                    params: list[Any] = []
                    for k, v in patch.items():
                        params.append(json.dumps(v) if k == "question_data" else v)
                        cast = "::jsonb" if k == "question_data" else ""
                        set_parts.append(f"{k} = ${len(params)}{cast}")
                    params.append(qid)
                    row = await conn.fetchrow(
                        f"""
                        UPDATE public.questions
                        SET {', '.join(set_parts)}
                        WHERE id = ${len(params)} AND deleted_at IS NULL
                        RETURNING {_SELECT_COLS}
                        """,
                        *params,
                    )
                    out.append(_row_to_question(row))
        logger.info("Bulk-updated %d questions", len(out))
        return out

    async def bulk_delete_questions(
        self, ids: list[str], hard: bool = False
    ) -> None:
        """Delete N questions in one transaction. Soft by default.

        Raises:
            ValidationError: empty list.
            NotFoundError: any id missing (entire batch rolled back).
        """
        if not ids:
            raise ValidationError("Empty batch")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                if hard:
                    result = await conn.execute(
                        "DELETE FROM public.questions WHERE id = ANY($1::uuid[])",
                        ids,
                    )
                else:
                    result = await conn.execute(
                        """
                        UPDATE public.questions
                        SET deleted_at = now()
                        WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL
                        """,
                        ids,
                    )
                affected = int(result.split()[-1]) if result else 0
                if affected != len(ids):
                    raise NotFoundError(
                        f"Some question IDs were not found "
                        f"({affected}/{len(ids)} matched)"
                    )
        logger.info(
            "Bulk-%s-deleted %d questions",
            "hard" if hard else "soft", len(ids),
        )

    async def hard_delete_question(self, question_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM public.questions WHERE id = $1",
                question_id,
            )
        deleted = int(result.split()[-1]) if result else 0
        if deleted == 0:
            logger.warning("hard_delete_question: question %s not found", question_id)
            raise NotFoundError(f"Question {question_id} not found")
        logger.info("Hard-deleted question %s (CASCADE answers)", question_id)


question_service = QuestionService()
