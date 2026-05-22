import json
import logging
from typing import Any, Optional

from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from services.subscription_plans import SUBSCRIPTION_PLANS, PlanTier
from utils.grading_utils import grade_question, strip_correct

logger = logging.getLogger(__name__)


def _coerce_jsonb(raw):
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _row_to_attempt(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "exam_id": str(row["exam_id"]),
        "score": float(row["score"]) if row["score"] is not None else None,
        "total_points": float(row["total_points"]) if row["total_points"] is not None else None,
        "percentage": float(row["percentage"]) if row["percentage"] is not None else None,
        "time_spent_seconds": row["time_spent_seconds"],
        "audio_play_count": row["audio_play_count"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
    }


_ATTEMPT_COLS = """
    id, user_id, exam_id, score, total_points, percentage,
    time_spent_seconds, audio_play_count, started_at, submitted_at
"""


class AttemptLimitExceededError(PermissionDeniedError):
    """Raised when a user has reached their tier's monthly attempt limit."""


class AudioPlayLimitExceededError(PermissionDeniedError):
    """Raised when student attempts to play listening audio beyond max_audio_plays."""


class AttemptService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def start_attempt(self, user_id: str, exam_id: str) -> dict[str, Any]:
        """Create a new attempt + return exam + questions (correct stripped).

        Raises:
            NotFoundError: exam doesn't exist or isn't published.
            AttemptLimitExceededError: user hit their tier's monthly cap.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    """
                    SELECT id, title, level, skill, duration_minutes,
                           description, audio_url, passage, max_audio_plays,
                           is_published
                    FROM public.exams
                    WHERE id = $1 AND deleted_at IS NULL
                    """,
                    exam_id,
                )
                if not exam or not exam["is_published"]:
                    logger.warning("start_attempt: exam %s not found or not published", exam_id)
                    raise NotFoundError(f"Exam {exam_id} not found")

                await self._enforce_tier_limit(conn, user_id)

                row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.attempts (user_id, exam_id)
                    VALUES ($1, $2)
                    RETURNING {_ATTEMPT_COLS}
                    """,
                    user_id,
                    exam_id,
                )

                qrows = await conn.fetch(
                    """
                    SELECT id, position, question_type, question_data, points
                    FROM public.questions
                    WHERE exam_id = $1 AND deleted_at IS NULL
                    ORDER BY position ASC, created_at ASC
                    """,
                    exam_id,
                )

        questions = [
            {
                "id": str(q["id"]),
                "position": q["position"],
                "question_type": q["question_type"],
                "question_data": strip_correct(
                    q["question_type"], _coerce_jsonb(q["question_data"])
                ),
                "points": q["points"],
            }
            for q in qrows
        ]

        logger.info(
            "Started attempt %s for user %s on exam %s (%d questions)",
            row["id"], user_id, exam_id, len(questions),
        )
        return {
            "attempt": _row_to_attempt(row),
            "exam": {
                "id": str(exam["id"]),
                "title": exam["title"],
                "level": exam["level"],
                "skill": exam["skill"],
                "durationMinutes": exam["duration_minutes"],
                "description": exam["description"],
                "audioUrl": exam["audio_url"],
                "passage": exam["passage"],
                "maxAudioPlays": exam["max_audio_plays"],
            },
            "questions": questions,
        }

    async def _enforce_tier_limit(self, conn, user_id: str) -> None:
        sub = await conn.fetchrow(
            """
            SELECT tier, current_period_start
            FROM public.subscriptions
            WHERE user_id = $1
            """,
            user_id,
        )
        if not sub:
            # No subscription row (shouldn't happen — defensive). Skip enforcement.
            return

        try:
            tier_enum = PlanTier(sub["tier"])
        except ValueError:
            return  # unknown tier — skip enforcement

        plan = SUBSCRIPTION_PLANS.get(tier_enum)
        if not plan or plan.attempts_monthly < 0:
            return  # unlimited

        used = await conn.fetchval(
            """
            SELECT COUNT(*) FROM public.attempts
            WHERE user_id = $1 AND started_at >= $2
            """,
            user_id,
            sub["current_period_start"],
        )
        if used >= plan.attempts_monthly:
            logger.warning(
                "start_attempt: user %s hit limit (tier=%s, used=%d/%d)",
                user_id, sub["tier"], used, plan.attempts_monthly,
            )
            raise AttemptLimitExceededError(
                f"Monthly attempt limit reached ({plan.attempts_monthly} attempts)"
            )

    async def submit_attempt(
        self,
        attempt_id: str,
        user_id: str,
        answers: list[dict[str, Any]],
        time_spent_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Grade + persist answers, finalize the attempt.

        Args:
            attempt_id: attempt row UUID.
            user_id: must equal attempts.user_id (owner check).
            answers: list of {"questionId": "...", "studentAnswer": ...}.
            time_spent_seconds: optional duration in seconds.

        Raises:
            NotFoundError: attempt doesn't exist.
            PermissionDeniedError: user is not the owner.
            ValidationError: attempt already submitted.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                attempt = await conn.fetchrow(
                    f"""
                    SELECT {_ATTEMPT_COLS}, e.title AS exam_title
                    FROM public.attempts a
                    JOIN public.exams e ON e.id = a.exam_id
                    WHERE a.id = $1
                    """,
                    attempt_id,
                )
                if not attempt:
                    logger.warning("submit_attempt: attempt %s not found", attempt_id)
                    raise NotFoundError(f"Attempt {attempt_id} not found")
                if str(attempt["user_id"]) != user_id:
                    logger.warning(
                        "submit_attempt: user %s not owner of attempt %s",
                        user_id, attempt_id,
                    )
                    raise PermissionDeniedError("Not the owner of this attempt")
                if attempt["submitted_at"] is not None:
                    raise ValidationError("Attempt already submitted")

                # Index answers by question_id for O(1) lookup
                answers_by_qid = {a["questionId"]: a["studentAnswer"] for a in answers}

                qrows = await conn.fetch(
                    """
                    SELECT id, question_type, question_data, points
                    FROM public.questions
                    WHERE exam_id = $1 AND deleted_at IS NULL
                    """,
                    attempt["exam_id"],
                )

                total_points = 0
                earned = 0
                answer_inserts = []
                for q in qrows:
                    qid = str(q["id"])
                    qtype = q["question_type"]
                    qdata = _coerce_jsonb(q["question_data"])
                    qpoints = q["points"]
                    total_points += qpoints

                    student_answer = answers_by_qid.get(qid)
                    is_correct = grade_question(qtype, qdata, student_answer)
                    points_earned = qpoints if is_correct else 0
                    earned += points_earned

                    answer_inserts.append(
                        (q["id"], student_answer, is_correct, points_earned)
                    )

                # Bulk insert answers
                for q_uuid, sa, ic, pe in answer_inserts:
                    await conn.execute(
                        """
                        INSERT INTO public.answers
                            (attempt_id, question_id, student_answer, is_correct, points_earned)
                        VALUES ($1, $2, $3::jsonb, $4, $5)
                        """,
                        attempt_id,
                        q_uuid,
                        json.dumps(sa) if sa is not None else None,
                        ic,
                        pe,
                    )

                percentage = (earned / total_points * 100) if total_points > 0 else 0
                row = await conn.fetchrow(
                    f"""
                    UPDATE public.attempts
                    SET score = $2, total_points = $3, percentage = $4,
                        time_spent_seconds = $5, submitted_at = now()
                    WHERE id = $1
                    RETURNING {_ATTEMPT_COLS}
                    """,
                    attempt_id,
                    earned,
                    total_points,
                    round(percentage, 2),
                    time_spent_seconds,
                )

        logger.info(
            "Submitted attempt %s: %d/%d (%.1f%%)",
            attempt_id, earned, total_points, percentage,
        )
        return _row_to_attempt(row)

    async def get_attempt_with_answers(
        self, attempt_id: str
    ) -> Optional[dict[str, Any]]:
        """Return attempt + per-answer breakdown joined with question metadata."""
        async with self.db.acquire() as conn:
            attempt = await conn.fetchrow(
                f"""
                SELECT {_ATTEMPT_COLS},
                       e.title AS exam_title, e.level AS exam_level, e.skill AS exam_skill
                FROM public.attempts a
                JOIN public.exams e ON e.id = a.exam_id
                WHERE a.id = $1
                """,
                attempt_id,
            )
            if not attempt:
                return None

            answer_rows = await conn.fetch(
                """
                SELECT a.id AS answer_id, a.student_answer, a.is_correct, a.points_earned,
                       q.id AS question_id, q.position, q.question_type, q.question_data,
                       q.points
                FROM public.answers a
                JOIN public.questions q ON q.id = a.question_id
                WHERE a.attempt_id = $1
                ORDER BY q.position ASC
                """,
                attempt_id,
            )

        is_submitted = attempt["submitted_at"] is not None
        per_answer = []
        for ar in answer_rows:
            qdata = _coerce_jsonb(ar["question_data"])
            # If still in progress, hide correct answers
            if not is_submitted:
                qdata = strip_correct(ar["question_type"], qdata)
            per_answer.append(
                {
                    "answer_id": str(ar["answer_id"]),
                    "question_id": str(ar["question_id"]),
                    "position": ar["position"],
                    "question_type": ar["question_type"],
                    "question_data": qdata,
                    "points": ar["points"],
                    "student_answer": _coerce_jsonb(ar["student_answer"]),
                    "is_correct": ar["is_correct"],
                    "points_earned": ar["points_earned"],
                }
            )

        return {
            "attempt": _row_to_attempt(attempt),
            "exam": {
                "id": str(attempt["exam_id"]),
                "title": attempt["exam_title"],
                "level": attempt["exam_level"],
                "skill": attempt["exam_skill"],
            },
            "answers": per_answer,
        }

    async def list_history_for_user(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_ATTEMPT_COLS},
                       e.title AS exam_title, e.level AS exam_level, e.skill AS exam_skill
                FROM public.attempts a
                JOIN public.exams e ON e.id = a.exam_id
                WHERE a.user_id = $1
                ORDER BY a.started_at DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
        return [
            {
                **_row_to_attempt(r),
                "exam_title": r["exam_title"],
                "exam_level": r["exam_level"],
                "exam_skill": r["exam_skill"],
            }
            for r in rows
        ]

    async def record_audio_play(
        self, attempt_id: str, user_id: str
    ) -> dict[str, Any]:
        """Increment audio_play_count; reject if it would exceed exam.max_audio_plays.

        Raises:
            NotFoundError: attempt doesn't exist.
            PermissionDeniedError: user is not the owner.
            ValidationError: attempt already submitted, or exam is not a listening exam.
            AudioPlayLimitExceededError: cap reached.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT a.id, a.user_id, a.submitted_at, a.audio_play_count,
                           e.skill, e.max_audio_plays
                    FROM public.attempts a
                    JOIN public.exams e ON e.id = a.exam_id
                    WHERE a.id = $1
                    """,
                    attempt_id,
                )
                if not row:
                    raise NotFoundError(f"Attempt {attempt_id} not found")
                if str(row["user_id"]) != user_id:
                    raise PermissionDeniedError("Not the owner of this attempt")
                if row["submitted_at"] is not None:
                    raise ValidationError("Attempt already submitted")
                if row["skill"] != "listening":
                    raise ValidationError("Audio playback only applies to listening exams")
                if row["audio_play_count"] >= row["max_audio_plays"]:
                    logger.warning(
                        "record_audio_play: cap reached for attempt %s (count=%d, max=%d)",
                        attempt_id, row["audio_play_count"], row["max_audio_plays"],
                    )
                    raise AudioPlayLimitExceededError(
                        f"Audio play limit reached ({row['max_audio_plays']})"
                    )

                updated = await conn.fetchrow(
                    """
                    UPDATE public.attempts
                    SET audio_play_count = audio_play_count + 1
                    WHERE id = $1
                    RETURNING audio_play_count
                    """,
                    attempt_id,
                )

        new_count = updated["audio_play_count"]
        remaining = row["max_audio_plays"] - new_count
        logger.info(
            "record_audio_play: attempt %s now at %d/%d plays",
            attempt_id, new_count, row["max_audio_plays"],
        )
        return {
            "audioPlayCount": new_count,
            "maxAudioPlays": row["max_audio_plays"],
            "remainingPlays": remaining,
        }


attempt_service = AttemptService()
