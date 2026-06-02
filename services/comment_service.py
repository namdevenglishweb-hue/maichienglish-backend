"""Teacher comment service — writing range comments + speaking overall comment.

See WRITING_SPEAKING.md §9.
"""

import logging
from typing import Any, Optional

from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

logger = logging.getLogger(__name__)


def _row_to_writing_comment(r) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "range_start": r["range_start"],
        "range_end": r["range_end"],
        "quoted_text": r["quoted_text"],
        "comment_text": r["comment_text"],
        "created_by": str(r["created_by"]) if r["created_by"] else None,
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }


class CommentService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    # ------------------------------------------------------------------ #
    # Shared precondition: attempt is submitted, not abandoned, not       #
    # fully-graded yet. Comment edits lock alongside grading.             #
    # ------------------------------------------------------------------ #
    async def _load_answer_for_edit(
        self, conn, attempt_id: str, answer_id: str, expected_type: str
    ) -> dict[str, Any]:
        row = await conn.fetchrow(
            """
            SELECT a.id, a.attempt_id, a.student_answer,
                   q.question_type,
                   att.submitted_at, att.is_abandoned, att.is_fully_graded
            FROM public.answers a
            JOIN public.questions q ON q.id = a.question_id
            JOIN public.attempts att ON att.id = a.attempt_id
            WHERE a.id = $1
            FOR UPDATE OF a
            """,
            answer_id,
        )
        if not row or str(row["attempt_id"]) != str(attempt_id):
            raise NotFoundError(f"Answer {answer_id} not found in attempt {attempt_id}")
        if row["question_type"] != expected_type:
            raise ValidationError(
                f"Endpoint is for {expected_type} answers only "
                f"(found {row['question_type']})"
            )
        if row["submitted_at"] is None:
            raise ValidationError("Attempt is not submitted")
        if row["is_abandoned"]:
            raise ValidationError("Attempt is abandoned")
        if row["is_fully_graded"]:
            raise ValidationError(
                "Attempt is already fully graded; comments locked"
            )
        return row

    # ================================================================== #
    # Writing range comments — POST / PUT / DELETE                       #
    # ================================================================== #

    async def create_writing_comment(
        self,
        attempt_id: str,
        answer_id: str,
        teacher_id: str,
        range_start: int,
        range_end: int,
        quoted_text: str,
        comment_text: str,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                ans = await self._load_answer_for_edit(
                    conn, attempt_id, answer_id, "writing"
                )

                # Validate range bounds vs the student's text length.
                from services.attempt_service import _coerce_jsonb
                sa = _coerce_jsonb(ans["student_answer"]) or {}
                text = (sa or {}).get("text") or ""
                if range_end > len(text):
                    raise ValidationError(
                        f"Range end {range_end} exceeds text length {len(text)}"
                    )

                # Strict consistency: quotedText must match raw slice.
                if quoted_text != text[range_start:range_end]:
                    raise ValidationError(
                        "quotedText does not match the selected range"
                    )

                # Overlap check (after row-lock above). Two ranges overlap
                # iff range_start < new_end AND new_start < range_end.
                conflicts = await conn.fetch(
                    """
                    SELECT id FROM public.writing_comments
                    WHERE answer_id = $1
                      AND range_start < $3
                      AND $2 < range_end
                    """,
                    answer_id, range_start, range_end,
                )
                if conflicts:
                    ids = [str(c["id"]) for c in conflicts]
                    raise ValidationError(
                        f"Comment range overlaps existing comment(s): {ids}"
                    )

                row = await conn.fetchrow(
                    """
                    INSERT INTO public.writing_comments
                        (answer_id, range_start, range_end,
                         quoted_text, comment_text, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id, range_start, range_end, quoted_text,
                              comment_text, created_by, created_at, updated_at
                    """,
                    answer_id, range_start, range_end,
                    quoted_text, comment_text, teacher_id,
                )
        logger.info(
            "Added writing comment %s on answer %s by teacher %s",
            row["id"], answer_id, teacher_id,
        )
        return _row_to_writing_comment(row)

    async def update_writing_comment(
        self,
        attempt_id: str,
        answer_id: str,
        comment_id: str,
        teacher_id: str,
        comment_text: str,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._load_answer_for_edit(
                    conn, attempt_id, answer_id, "writing"
                )
                row = await conn.fetchrow(
                    """
                    UPDATE public.writing_comments
                    SET comment_text = $1, updated_at = now()
                    WHERE id = $2 AND answer_id = $3
                    RETURNING id, range_start, range_end, quoted_text,
                              comment_text, created_by, created_at, updated_at
                    """,
                    comment_text, comment_id, answer_id,
                )
                if not row:
                    raise NotFoundError(f"Comment {comment_id} not found")
        logger.info(
            "Updated writing comment %s by teacher %s", comment_id, teacher_id,
        )
        return _row_to_writing_comment(row)

    async def delete_writing_comment(
        self,
        attempt_id: str,
        answer_id: str,
        comment_id: str,
    ) -> None:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._load_answer_for_edit(
                    conn, attempt_id, answer_id, "writing"
                )
                result = await conn.execute(
                    """
                    DELETE FROM public.writing_comments
                    WHERE id = $1 AND answer_id = $2
                    """,
                    comment_id, answer_id,
                )
                if result.split()[-1] == "0":
                    raise NotFoundError(f"Comment {comment_id} not found")

    # ================================================================== #
    # Speaking single overall comment — PUT (UPSERT) / DELETE             #
    # ================================================================== #

    async def upsert_speaking_comment(
        self,
        attempt_id: str,
        answer_id: str,
        teacher_id: str,
        comment_text: str,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._load_answer_for_edit(
                    conn, attempt_id, answer_id, "speaking"
                )
                row = await conn.fetchrow(
                    """
                    UPDATE public.answers
                    SET speaking_comment    = $2,
                        speaking_comment_by = $3,
                        speaking_comment_at = now()
                    WHERE id = $1
                    RETURNING speaking_comment, speaking_comment_by,
                              speaking_comment_at
                    """,
                    answer_id, comment_text, teacher_id,
                )
        logger.info(
            "Set speaking comment on answer %s by teacher %s",
            answer_id, teacher_id,
        )
        return {
            "comment_text": row["speaking_comment"],
            "created_by": str(row["speaking_comment_by"]) if row["speaking_comment_by"] else None,
            "created_at": row["speaking_comment_at"].isoformat(),
        }

    async def delete_speaking_comment(
        self,
        attempt_id: str,
        answer_id: str,
    ) -> None:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._load_answer_for_edit(
                    conn, attempt_id, answer_id, "speaking"
                )
                row = await conn.fetchrow(
                    """
                    UPDATE public.answers
                    SET speaking_comment    = NULL,
                        speaking_comment_by = NULL,
                        speaking_comment_at = NULL
                    WHERE id = $1
                      AND (speaking_comment IS NOT NULL
                           OR speaking_comment_by IS NOT NULL
                           OR speaking_comment_at IS NOT NULL)
                    RETURNING id
                    """,
                    answer_id,
                )
                if not row:
                    raise NotFoundError(f"No speaking comment to clear on answer {answer_id}")


comment_service = CommentService()
