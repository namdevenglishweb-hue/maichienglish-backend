import json
import logging
from typing import Any, Optional

import asyncpg

from services.exceptions import (
    ConflictError,
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
        "is_abandoned": row["is_abandoned"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
    }


# Unqualified column list — for INSERT/UPDATE RETURNING (no table alias).
_ATTEMPT_COLS = """
    id, user_id, exam_id, score, total_points, percentage,
    time_spent_seconds, is_abandoned, started_at, submitted_at
"""

# Same columns aliased with `a.` — required for SELECTs that JOIN exams,
# otherwise `id` is ambiguous against `e.id`.
_ATTEMPT_COLS_A = """
    a.id, a.user_id, a.exam_id, a.score, a.total_points, a.percentage,
    a.time_spent_seconds, a.is_abandoned, a.started_at, a.submitted_at
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

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    async def _fetch_active_attempt(self, conn, user_id: str):
        """Return the user's single active (= not submitted, not abandoned)
        attempt row, or None. Enforced 1-per-user by partial unique index
        `attempts_one_active_per_user`."""
        return await conn.fetchrow(
            f"""
            SELECT {_ATTEMPT_COLS}
            FROM public.attempts
            WHERE user_id = $1
              AND submitted_at IS NULL
              AND NOT is_abandoned
            """,
            user_id,
        )

    async def _fetch_exam_tree(self, conn, exam_id: str) -> dict[str, Any]:
        """Load exam metadata + sections + questions for the student-facing
        attempt view. Correct-answer fields are stripped from every question."""
        exam = await conn.fetchrow(
            """
            SELECT id, title, level, skill, duration_minutes, description
            FROM public.exams
            WHERE id = $1 AND deleted_at IS NULL
            """,
            exam_id,
        )
        # Caller is expected to have already validated existence — defensive.
        if not exam:
            raise NotFoundError(f"Exam {exam_id} not found")

        section_rows = await conn.fetch(
            """
            SELECT id, position, part_label, type, instructions,
                   materials, max_audio_plays
            FROM public.sections
            WHERE exam_id = $1 AND deleted_at IS NULL
            ORDER BY position ASC, created_at ASC
            """,
            exam_id,
        )
        section_ids = [r["id"] for r in section_rows]
        if section_ids:
            qrows = await conn.fetch(
                """
                SELECT id, section_id, position, question_type,
                       question_data, points
                FROM public.questions
                WHERE section_id = ANY($1::uuid[]) AND deleted_at IS NULL
                ORDER BY position ASC, created_at ASC
                """,
                section_ids,
            )
        else:
            qrows = []

        q_by_section: dict[str, list[dict[str, Any]]] = {}
        for q in qrows:
            sid = str(q["section_id"])
            q_by_section.setdefault(sid, []).append(
                {
                    "id": str(q["id"]),
                    "position": q["position"],
                    "questionType": q["question_type"],
                    "questionData": strip_correct(
                        q["question_type"], _coerce_jsonb(q["question_data"])
                    ),
                    "points": q["points"],
                }
            )

        sections_payload = [
            {
                "id": str(s["id"]),
                "position": s["position"],
                "partLabel": s["part_label"],
                "type": s["type"],
                "instructions": s["instructions"],
                "materials": _coerce_jsonb(s["materials"]) or [],
                "maxAudioPlays": s["max_audio_plays"],
                "questions": q_by_section.get(str(s["id"]), []),
            }
            for s in section_rows
        ]

        return {
            "id": str(exam["id"]),
            "title": exam["title"],
            "level": exam["level"],
            "skill": exam["skill"],
            "durationMinutes": exam["duration_minutes"],
            "description": exam["description"],
            "sections": sections_payload,
        }

    async def _fetch_saved_answers(
        self, conn, attempt_id: str, exam_id: str
    ) -> list[dict[str, Any]]:
        """Return previously-saved answers for this attempt, filtered to
        questions still present in the exam tree (defense-in-depth against
        admin hard-deletes between save and resume — see ATTEMPT_LIFECYCLE.md
        §4.1 Case B note + §7 edge case #6)."""
        rows = await conn.fetch(
            """
            SELECT a.question_id, a.student_answer
            FROM public.answers a
            JOIN public.questions q ON q.id = a.question_id
            JOIN public.sections s ON s.id = q.section_id
            WHERE a.attempt_id = $1
              AND s.exam_id = $2
              AND s.deleted_at IS NULL
              AND q.deleted_at IS NULL
            """,
            attempt_id,
            exam_id,
        )
        return [
            {
                "questionId": str(r["question_id"]),
                "studentAnswer": _coerce_jsonb(r["student_answer"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    async def start_attempt(self, user_id: str, exam_id: str) -> dict[str, Any]:
        """Idempotent start. Three outcomes (see ATTEMPT_LIFECYCLE.md §4.1):

          - Case A: no active attempt → INSERT new, consume quota, isResume=False.
          - Case B: active attempt for the SAME exam → return existing
            + savedAnswers (no quota consumed), isResume=True.
          - Case C: active attempt for a DIFFERENT exam → ConflictError (409).

        Race-safe: a concurrent INSERT loser triggers `UniqueViolationError`
        on the `attempts_one_active_per_user` partial unique index. We catch
        it, re-fetch the now-existing active attempt, and re-dispatch as
        Case B or Case C.

        Raises:
            NotFoundError: exam doesn't exist or isn't published.
            ConflictError: active attempt is for a different exam (Case C).
            AttemptLimitExceededError: user hit their tier's monthly cap.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Pre-check active attempt — fast path for both resume and
                # conflict, and avoids touching the exam table when we
                # already know we're going to bail.
                active = await self._fetch_active_attempt(conn, user_id)
                if active is not None:
                    if str(active["exam_id"]) == exam_id:
                        return await self._build_resume_payload(conn, active)
                    raise ConflictError(
                        "You have an unfinished attempt for another exam"
                    )

                # No active attempt — verify exam, enforce quota, INSERT.
                exam = await conn.fetchrow(
                    """
                    SELECT id, is_published
                    FROM public.exams
                    WHERE id = $1 AND deleted_at IS NULL
                    """,
                    exam_id,
                )
                if not exam or not exam["is_published"]:
                    logger.warning(
                        "start_attempt: exam %s not found or not published",
                        exam_id,
                    )
                    raise NotFoundError(f"Exam {exam_id} not found")

                await self._enforce_tier_limit(conn, user_id)

                try:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO public.attempts (user_id, exam_id)
                        VALUES ($1, $2)
                        RETURNING {_ATTEMPT_COLS}
                        """,
                        user_id,
                        exam_id,
                    )
                except asyncpg.exceptions.UniqueViolationError:
                    # Lost the race against a concurrent POST /attempts.
                    # Re-fetch the now-existing active attempt and resolve.
                    logger.info(
                        "start_attempt: race detected for user %s, re-resolving",
                        user_id,
                    )
                    active = await self._fetch_active_attempt(conn, user_id)
                    if active is None:
                        # Extremely unlikely — winner submitted/abandoned
                        # between our INSERT and SELECT. Surface as conflict
                        # so the FE refreshes /active and retries.
                        raise ConflictError(
                            "Conflicting attempt; please retry"
                        ) from None
                    if str(active["exam_id"]) == exam_id:
                        return await self._build_resume_payload(conn, active)
                    raise ConflictError(
                        "You have an unfinished attempt for another exam"
                    ) from None

                exam_tree = await self._fetch_exam_tree(conn, exam_id)

        total_questions = sum(len(s["questions"]) for s in exam_tree["sections"])
        logger.info(
            "Started attempt %s for user %s on exam %s (%d sections, %d questions)",
            row["id"], user_id, exam_id, len(exam_tree["sections"]), total_questions,
        )
        return {
            "is_resume": False,
            "attempt": _row_to_attempt(row),
            "exam": exam_tree,
            "saved_answers": [],
        }

    async def _build_resume_payload(self, conn, active_row) -> dict[str, Any]:
        """Construct the Case B (resume) response from an existing active
        attempt row. Quota is NOT touched."""
        exam_id = str(active_row["exam_id"])
        attempt_id = str(active_row["id"])
        exam_tree = await self._fetch_exam_tree(conn, exam_id)
        saved = await self._fetch_saved_answers(conn, attempt_id, exam_id)
        logger.info(
            "Resumed attempt %s (user %s, exam %s, %d saved answers)",
            attempt_id, active_row["user_id"], exam_id, len(saved),
        )
        return {
            "is_resume": True,
            "attempt": _row_to_attempt(active_row),
            "exam": exam_tree,
            "saved_answers": saved,
        }

    async def get_active_attempt(self, user_id: str) -> Optional[dict[str, Any]]:
        """Return a summary of the user's single active attempt, or None.

        Powers GET /api/attempts/active — the FE's single source of truth
        for "is there an attempt in progress?" (see ATTEMPT_LIFECYCLE.md
        §4.2 and §6.1)."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.id, a.exam_id, a.started_at,
                       e.title AS exam_title, e.level AS exam_level,
                       e.skill AS exam_skill,
                       (SELECT COUNT(*) FROM public.answers ans
                          WHERE ans.attempt_id = a.id)::int AS saved_answer_count
                FROM public.attempts a
                JOIN public.exams e ON e.id = a.exam_id
                WHERE a.user_id = $1
                  AND a.submitted_at IS NULL
                  AND NOT a.is_abandoned
                """,
                user_id,
            )
        if not row:
            return None
        return {
            "attemptId": str(row["id"]),
            "examId": str(row["exam_id"]),
            "examTitle": row["exam_title"],
            "examLevel": row["exam_level"],
            "examSkill": row["exam_skill"],
            "startedAt": row["started_at"].isoformat() if row["started_at"] else None,
            "savedAnswerCount": row["saved_answer_count"],
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
            return

        try:
            tier_enum = PlanTier(sub["tier"])
        except ValueError:
            return

        plan = SUBSCRIPTION_PLANS.get(tier_enum)
        if not plan or plan.attempts_monthly < 0:
            return

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

    async def save_answers(
        self,
        attempt_id: str,
        user_id: str,
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """UPSERT mid-attempt answers without grading (PATCH /attempts/{id}/answers).

        Each row is stored with `is_correct = NULL` and `points_earned = 0`
        to signal "saved but ungraded". Submit later overwrites these with
        graded values.

        Per ATTEMPT_LIFECYCLE.md §4.3, we validate the `questionId` belongs
        to this attempt's exam; we do NOT validate the shape of
        `studentAnswer` (deferred to grading on submit).

        Raises:
            NotFoundError: attempt or referenced question missing.
            PermissionDeniedError: caller is not the owner.
            ValidationError: attempt already submitted or abandoned.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                attempt = await conn.fetchrow(
                    f"""
                    SELECT {_ATTEMPT_COLS}
                    FROM public.attempts
                    WHERE id = $1
                    """,
                    attempt_id,
                )
                if not attempt:
                    raise NotFoundError(f"Attempt {attempt_id} not found")
                if str(attempt["user_id"]) != user_id:
                    raise PermissionDeniedError("Not the owner of this attempt")
                if attempt["is_abandoned"]:
                    raise ValidationError("Attempt is abandoned")
                if attempt["submitted_at"] is not None:
                    raise ValidationError("Attempt already submitted")

                # Validate every questionId belongs to this attempt's exam.
                if answers:
                    question_ids = [a["questionId"] for a in answers]
                    valid_rows = await conn.fetch(
                        """
                        SELECT q.id::text AS qid
                        FROM public.questions q
                        JOIN public.sections s ON s.id = q.section_id
                        WHERE q.id = ANY($1::uuid[])
                          AND s.exam_id = $2
                          AND s.deleted_at IS NULL
                          AND q.deleted_at IS NULL
                        """,
                        question_ids,
                        attempt["exam_id"],
                    )
                    valid_qids = {r["qid"] for r in valid_rows}
                    missing = [qid for qid in question_ids if qid not in valid_qids]
                    if missing:
                        raise NotFoundError(
                            f"Question(s) not in this attempt's exam: {missing[:3]}"
                            + (" …" if len(missing) > 3 else "")
                        )

                    for a in answers:
                        sa = a.get("studentAnswer")
                        await conn.execute(
                            """
                            INSERT INTO public.answers
                                (attempt_id, question_id, student_answer,
                                 is_correct, points_earned)
                            VALUES ($1, $2, $3::jsonb, NULL, 0)
                            ON CONFLICT (attempt_id, question_id) DO UPDATE
                            SET student_answer = EXCLUDED.student_answer,
                                is_correct = NULL,
                                points_earned = 0
                            """,
                            attempt_id,
                            a["questionId"],
                            json.dumps(sa) if sa is not None else None,
                        )

                # For the response — how many total questions in this exam,
                # and how many have a saved answer row now.
                total_questions = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    WHERE s.exam_id = $1
                      AND s.deleted_at IS NULL
                      AND q.deleted_at IS NULL
                    """,
                    attempt["exam_id"],
                )
                saved_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM public.answers
                    WHERE attempt_id = $1
                    """,
                    attempt_id,
                )

        logger.info(
            "save_answers: attempt %s saved %d new (total saved now %d/%d)",
            attempt_id, len(answers), saved_count, total_questions,
        )
        return {
            "savedCount": saved_count,
            "totalQuestions": total_questions,
        }

    async def abandon_attempt(
        self, attempt_id: str, user_id: str
    ) -> dict[str, Any]:
        """Permanently mark the attempt as abandoned (score=0). Frees the
        "1 active globally" slot but still counts toward the monthly quota.

        Raises:
            NotFoundError: attempt missing.
            PermissionDeniedError: caller is not the owner.
            ValidationError: attempt already submitted or already abandoned.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                attempt = await conn.fetchrow(
                    f"""
                    SELECT {_ATTEMPT_COLS}
                    FROM public.attempts
                    WHERE id = $1
                    """,
                    attempt_id,
                )
                if not attempt:
                    raise NotFoundError(f"Attempt {attempt_id} not found")
                if str(attempt["user_id"]) != user_id:
                    raise PermissionDeniedError("Not the owner of this attempt")
                if attempt["is_abandoned"]:
                    raise ValidationError("Attempt already abandoned")
                if attempt["submitted_at"] is not None:
                    raise ValidationError("Attempt already submitted")

                row = await conn.fetchrow(
                    f"""
                    UPDATE public.attempts
                    SET is_abandoned = true,
                        submitted_at = now(),
                        score = 0,
                        total_points = 0,
                        percentage = 0
                    WHERE id = $1
                    RETURNING {_ATTEMPT_COLS}
                    """,
                    attempt_id,
                )

        logger.info("Abandoned attempt %s for user %s", attempt_id, user_id)
        return _row_to_attempt(row)

    async def submit_attempt(
        self,
        attempt_id: str,
        user_id: str,
        answers: list[dict[str, Any]],
        time_spent_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Grade + persist answers across all sections, finalize the attempt.

        Merges request body with any previously-saved answers (PATCH
        /answers): body answers override saved ones; saved answers not in
        the body are kept and graded as-is.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                attempt = await conn.fetchrow(
                    f"""
                    SELECT {_ATTEMPT_COLS}
                    FROM public.attempts
                    WHERE id = $1
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
                if attempt["is_abandoned"]:
                    raise ValidationError("Attempt is abandoned")
                if attempt["submitted_at"] is not None:
                    raise ValidationError("Attempt already submitted")

                # Merge saved answers with body answers — body wins.
                saved_rows = await conn.fetch(
                    """
                    SELECT question_id::text AS qid, student_answer
                    FROM public.answers
                    WHERE attempt_id = $1
                    """,
                    attempt_id,
                )
                merged: dict[str, Any] = {
                    r["qid"]: _coerce_jsonb(r["student_answer"]) for r in saved_rows
                }
                for a in answers:
                    merged[a["questionId"]] = a["studentAnswer"]

                qrows = await conn.fetch(
                    """
                    SELECT q.id, q.question_type, q.question_data, q.points
                    FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    WHERE s.exam_id = $1
                      AND s.deleted_at IS NULL
                      AND q.deleted_at IS NULL
                    """,
                    attempt["exam_id"],
                )

                total_points = 0
                earned = 0
                for q in qrows:
                    qid = str(q["id"])
                    qtype = q["question_type"]
                    qdata = _coerce_jsonb(q["question_data"])
                    qpoints = q["points"]
                    total_points += qpoints

                    student_answer = merged.get(qid)
                    is_correct = grade_question(qtype, qdata, student_answer)
                    points_earned = qpoints if is_correct else 0
                    earned += points_earned

                    await conn.execute(
                        """
                        INSERT INTO public.answers
                            (attempt_id, question_id, student_answer,
                             is_correct, points_earned)
                        VALUES ($1, $2, $3::jsonb, $4, $5)
                        ON CONFLICT (attempt_id, question_id) DO UPDATE
                        SET student_answer = EXCLUDED.student_answer,
                            is_correct     = EXCLUDED.is_correct,
                            points_earned  = EXCLUDED.points_earned
                        """,
                        attempt_id,
                        q["id"],
                        json.dumps(student_answer) if student_answer is not None else None,
                        is_correct,
                        points_earned,
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
        """Return attempt + per-answer breakdown joined with question + section metadata."""
        async with self.db.acquire() as conn:
            attempt = await conn.fetchrow(
                f"""
                SELECT {_ATTEMPT_COLS_A},
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
                       q.points, q.section_id, s.position AS section_position,
                       s.part_label AS section_part_label
                FROM public.answers a
                JOIN public.questions q ON q.id = a.question_id
                JOIN public.sections s ON s.id = q.section_id
                WHERE a.attempt_id = $1
                ORDER BY s.position ASC, q.position ASC
                """,
                attempt_id,
            )

        is_submitted = attempt["submitted_at"] is not None
        per_answer = []
        for ar in answer_rows:
            qdata = _coerce_jsonb(ar["question_data"])
            if not is_submitted:
                qdata = strip_correct(ar["question_type"], qdata)
            per_answer.append(
                {
                    "answer_id": str(ar["answer_id"]),
                    "question_id": str(ar["question_id"]),
                    "section_id": str(ar["section_id"]),
                    "section_position": ar["section_position"],
                    "section_part_label": ar["section_part_label"],
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
                SELECT {_ATTEMPT_COLS_A},
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
        self,
        attempt_id: str,
        section_id: str,
        material_index: int,
        user_id: str,
    ) -> dict[str, Any]:
        """Increment the per-audio counter for `materials[material_index]`
        inside `section_id` for this attempt. Rejects if it would exceed the
        section's `max_audio_plays` cap (shared across all audio materials,
        but counted independently per material).

        Counter is stored under `attempt_section_state.audio_play_counts`
        as a JSONB map keyed by the string form of material_index.

        Raises:
            NotFoundError: attempt missing, section missing, section not in
                this attempt's exam, or no material at `material_index`.
            PermissionDeniedError: user is not the owner.
            ValidationError: attempt already submitted or abandoned, or the
                material at `material_index` is not type=audio.
            AudioPlayLimitExceededError: cap reached for this material.
        """
        if material_index < 0:
            raise ValidationError("materialIndex must be non-negative")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                attempt = await conn.fetchrow(
                    """
                    SELECT id, user_id, exam_id, is_abandoned, submitted_at
                    FROM public.attempts
                    WHERE id = $1
                    """,
                    attempt_id,
                )
                if not attempt:
                    raise NotFoundError(f"Attempt {attempt_id} not found")
                if str(attempt["user_id"]) != user_id:
                    raise PermissionDeniedError("Not the owner of this attempt")
                if attempt["is_abandoned"]:
                    raise ValidationError("Attempt is abandoned")
                if attempt["submitted_at"] is not None:
                    raise ValidationError("Attempt already submitted")

                section = await conn.fetchrow(
                    """
                    SELECT id, exam_id, materials, max_audio_plays
                    FROM public.sections
                    WHERE id = $1 AND deleted_at IS NULL
                    """,
                    section_id,
                )
                if not section:
                    raise NotFoundError(f"Section {section_id} not found")
                if str(section["exam_id"]) != str(attempt["exam_id"]):
                    raise NotFoundError(
                        f"Section {section_id} not part of this attempt"
                    )

                materials = _coerce_jsonb(section["materials"]) or []
                if material_index >= len(materials):
                    raise NotFoundError(
                        f"Section has no material at index {material_index} "
                        f"(only {len(materials)} present)"
                    )
                material = materials[material_index]
                if not isinstance(material, dict) or material.get("type") != "audio":
                    raise ValidationError(
                        f"Material at index {material_index} is not audio "
                        f"(type={material.get('type') if isinstance(material, dict) else type(material).__name__!r})"
                    )

                max_plays = section["max_audio_plays"]
                key = str(material_index)
                # Atomic upsert + increment of the specific key inside the jsonb map.
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.attempt_section_state
                        (attempt_id, section_id, audio_play_counts, started_at)
                    VALUES ($1, $2, jsonb_build_object($3::text, 1), now())
                    ON CONFLICT (attempt_id, section_id) DO UPDATE SET
                        audio_play_counts = jsonb_set(
                            public.attempt_section_state.audio_play_counts,
                            ARRAY[$3::text],
                            to_jsonb(
                                COALESCE(
                                    (public.attempt_section_state.audio_play_counts->>$3::text)::int,
                                    0
                                ) + 1
                            )
                        ),
                        started_at = COALESCE(public.attempt_section_state.started_at, now())
                    RETURNING (audio_play_counts->>$3::text)::int AS new_count
                    """,
                    attempt_id,
                    section_id,
                    key,
                )
                new_count = row["new_count"]
                if max_plays is not None and new_count > max_plays:
                    logger.warning(
                        "record_audio_play: cap reached (attempt=%s, section=%s, "
                        "materialIndex=%d, %d>%d)",
                        attempt_id, section_id, material_index, new_count, max_plays,
                    )
                    raise AudioPlayLimitExceededError(
                        f"Audio play limit reached ({max_plays})"
                    )

        remaining = (max_plays - new_count) if max_plays is not None else None
        logger.info(
            "record_audio_play: attempt %s section %s materialIndex=%d at %d/%s plays",
            attempt_id, section_id, material_index, new_count, max_plays,
        )
        return {
            "materialIndex": material_index,
            "audioPlayCount": new_count,
            "maxPlays": max_plays,
            "remainingPlays": remaining,
        }


attempt_service = AttemptService()
