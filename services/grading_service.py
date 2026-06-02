"""Manual grading for writing/speaking question types.

Implements `POST /api/teacher/attempts/{id}/grade` business logic:
- validates each grade (questionId belongs, type is manual, points in range)
- UPSERTs answer rows with pointsEarned + is_correct=true
- recomputes attempt's score / total_points / percentage
- flips attempt.is_fully_graded → true when every manual question is graded
- schedules E5 student-notification email on the false→true transition

Locks the attempt row FOR UPDATE so two teachers grading concurrently
serialise.

See WRITING_SPEAKING.md §8.
"""

import logging
from typing import Any

from services.exceptions import (
    NotFoundError,
    ValidationError,
)
from utils.grading_utils import MANUAL_GRADE_TYPES

logger = logging.getLogger(__name__)


class GradingService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def grade_attempt(
        self,
        attempt_id: str,
        grades: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply a batch of grades to writing/speaking questions on an attempt.

        Args:
            attempt_id: the attempt being graded.
            grades: list of {"questionId": str, "pointsEarned": int}.

        Returns:
            {
                "graded": <int — count of grades applied in this call>,
                "attempt_score": float,
                "attempt_total_points": float,
                "attempt_percentage": float,
                "is_fully_graded": bool,
                "student_email": Optional[str]  # populated iff flag flipped
            }

        Raises:
            NotFoundError — attempt or any questionId not found
            ValidationError — attempt state wrong / question type non-manual / range
        """
        if not grades:
            raise ValidationError("grades must not be empty")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # ── Row-lock the attempt so two teachers don't race ──
                attempt = await conn.fetchrow(
                    """
                    SELECT id, exam_id, user_id, submitted_at,
                           is_abandoned, is_fully_graded
                    FROM public.attempts
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    attempt_id,
                )
                if not attempt:
                    raise NotFoundError(f"Attempt {attempt_id} not found")
                if attempt["submitted_at"] is None:
                    raise ValidationError("Attempt is not submitted")
                if attempt["is_abandoned"]:
                    raise ValidationError("Attempt is abandoned")
                if attempt["is_fully_graded"]:
                    raise ValidationError("Attempt is already fully graded")

                # ── Validate all questions belong + are manual + range OK ──
                question_ids = [g["questionId"] for g in grades]
                qrows = await conn.fetch(
                    """
                    SELECT q.id, q.question_type, q.points, s.exam_id
                    FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    WHERE q.id = ANY($1::uuid[])
                      AND q.deleted_at IS NULL
                      AND s.deleted_at IS NULL
                    """,
                    question_ids,
                )
                qmap = {str(q["id"]): q for q in qrows}
                missing = [qid for qid in question_ids if qid not in qmap]
                if missing:
                    raise NotFoundError(
                        f"Question(s) not in this attempt's exam: {missing}"
                    )
                for qid, q in qmap.items():
                    if str(q["exam_id"]) != str(attempt["exam_id"]):
                        raise NotFoundError(
                            f"Question(s) not in this attempt's exam: [{qid}]"
                        )
                    if q["question_type"] not in MANUAL_GRADE_TYPES:
                        raise ValidationError(
                            f"Question {qid} is not manually-graded"
                        )

                for g in grades:
                    q = qmap[g["questionId"]]
                    if g["pointsEarned"] > q["points"]:
                        raise ValidationError(
                            f"pointsEarned {g['pointsEarned']} exceeds max "
                            f"{q['points']} for question {g['questionId']}"
                        )

                # ── UPSERT each grade. is_correct=True conventionally
                #    signals "graded" (BE doesn't track partial-correct in v1). ──
                for g in grades:
                    await conn.execute(
                        """
                        INSERT INTO public.answers
                            (attempt_id, question_id,
                             is_correct, points_earned)
                        VALUES ($1, $2, true, $3)
                        ON CONFLICT (attempt_id, question_id) DO UPDATE
                        SET is_correct = true,
                            points_earned = EXCLUDED.points_earned
                        """,
                        attempt_id, g["questionId"], g["pointsEarned"],
                    )

                # ── Recompute attempt score / total / percentage. ──
                stats = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(a.points_earned), 0) AS earned,
                        COALESCE(SUM(q.points), 0)        AS total
                    FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    LEFT JOIN public.answers a
                      ON a.question_id = q.id AND a.attempt_id = $1
                    WHERE s.exam_id = $2
                      AND q.deleted_at IS NULL
                      AND s.deleted_at IS NULL
                    """,
                    attempt_id, attempt["exam_id"],
                )
                earned = int(stats["earned"] or 0)
                total = int(stats["total"] or 0)
                percentage = round((earned / total * 100) if total > 0 else 0, 2)

                # ── Is every manual question now graded? ──
                ungraded = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    LEFT JOIN public.answers a
                      ON a.question_id = q.id AND a.attempt_id = $1
                    WHERE s.exam_id = $2
                      AND q.deleted_at IS NULL
                      AND s.deleted_at IS NULL
                      AND q.question_type = ANY($3::text[])
                      AND (a.id IS NULL OR a.is_correct IS NULL)
                    """,
                    attempt_id, attempt["exam_id"], list(MANUAL_GRADE_TYPES),
                )
                fully_graded = (ungraded == 0)

                await conn.execute(
                    """
                    UPDATE public.attempts
                    SET score = $2, total_points = $3, percentage = $4,
                        is_fully_graded = $5
                    WHERE id = $1
                    """,
                    attempt_id, earned, total, percentage, fully_graded,
                )

                # If flag just flipped, fetch student email so the caller can
                # schedule the E5 notification.
                student_email = None
                if fully_graded and not attempt["is_fully_graded"]:
                    student_email = await conn.fetchval(
                        "SELECT email FROM public.profiles WHERE id = $1",
                        attempt["user_id"],
                    )

        logger.info(
            "Graded attempt %s: +%d questions, score=%d/%d (%.1f%%), fully=%s",
            attempt_id, len(grades), earned, total, percentage, fully_graded,
        )

        return {
            "graded": len(grades),
            "attempt_score": float(earned),
            "attempt_total_points": float(total),
            "attempt_percentage": percentage,
            "is_fully_graded": fully_graded,
            "student_email": student_email,
        }


grading_service = GradingService()
