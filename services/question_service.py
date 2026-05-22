import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError, model_validator

from services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-type question_data validators (kept in this module so other layers
# don't need to know the JSONB shape — see plan §3.4).
# ---------------------------------------------------------------------------


class _MultipleChoiceData(BaseModel):
    options: list[str] = Field(..., min_length=2)
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


class _MatchingData(BaseModel):
    left: list[str] = Field(..., min_length=1)
    right: list[str] = Field(..., min_length=1)
    correct_pairs: list[list[int]] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_pairs(self):
        for i, pair in enumerate(self.correct_pairs):
            if len(pair) != 2:
                raise ValueError(f"correct_pairs[{i}] must have exactly 2 indices")
            l_idx, r_idx = pair
            if not (0 <= l_idx < len(self.left)):
                raise ValueError(f"correct_pairs[{i}]: left index {l_idx} out of range")
            if not (0 <= r_idx < len(self.right)):
                raise ValueError(f"correct_pairs[{i}]: right index {r_idx} out of range")
        return self


_VALIDATORS = {
    "multiple_choice": _MultipleChoiceData,
    "fill_blank": _FillBlankData,
    "matching": _MatchingData,
}


def _validate_question_data(question_type: str, data: dict) -> dict:
    cls = _VALIDATORS.get(question_type)
    if cls is None:
        raise ValidationError(f"Unknown question_type '{question_type}'")
    try:
        return cls(**data).model_dump()
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
        "exam_id": str(row["exam_id"]),
        "position": row["position"],
        "question_type": row["question_type"],
        "question_data": _coerce_question_data(row["question_data"]),
        "points": row["points"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
    }


_SELECT_COLS = "id, exam_id, position, question_type, question_data, points, created_at, deleted_at"


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
        exam_id: str,
        question_type: str,
        question_data: dict,
        points: int = 1,
        position: Optional[int] = None,
    ) -> dict[str, Any]:
        validated_data = _validate_question_data(question_type, question_data)

        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    "SELECT id FROM public.exams WHERE id = $1 AND deleted_at IS NULL",
                    exam_id,
                )
                if not exam:
                    logger.warning("create_question: exam %s not found", exam_id)
                    raise NotFoundError(f"Exam {exam_id} not found")

                if position is None:
                    max_pos = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(position), 0)
                        FROM public.questions
                        WHERE exam_id = $1 AND deleted_at IS NULL
                        """,
                        exam_id,
                    )
                    position = max_pos + 1

                row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.questions
                        (exam_id, position, question_type, question_data, points)
                    VALUES ($1, $2, $3, $4::jsonb, $5)
                    RETURNING {_SELECT_COLS}
                    """,
                    exam_id,
                    position,
                    question_type,
                    json.dumps(validated_data),
                    points,
                )
        logger.info(
            "Created question %s (exam=%s, type=%s, position=%d)",
            row["id"], exam_id, question_type, position,
        )
        return _row_to_question(row)

    async def list_questions_by_exam(self, exam_id: str) -> list[dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.questions
                WHERE exam_id = $1 AND deleted_at IS NULL
                ORDER BY position ASC, created_at ASC
                """,
                exam_id,
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

    async def update_question(self, question_id: str, **fields) -> dict[str, Any]:
        if not fields:
            raise ValidationError("No fields to update")

        # Pull current row to merge type changes consistently
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
