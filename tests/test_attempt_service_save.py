"""Integration tests for attempt_service.save_answers (§8.4 ATTEMPT_LIFECYCLE.md).

Service-layer tests; the HTTP wrapping (PATCH /api/attempts/{id}/answers)
is covered in §8.11. All tests require a live Postgres; auto-skipped
without MAICHI_TEST_DB=1.

Contract recap (verified against services/attempt_service.py:390-499):
  - Input shape: answers = [{"questionId": str, "studentAnswer": Any}]
    (camelCase — matches AnswerInput.model_dump() from the route)
  - Output: {"savedCount": int, "totalQuestions": int}
  - Validation order: attempt exists → owner → not abandoned →
    not submitted → every questionId belongs to this attempt's exam
  - Per-row UPSERT: is_correct=NULL, points_earned=0
  - Empty answers list → no inserts; counts still computed
  - studentAnswer=None stored as SQL NULL (not the string "null")
"""

import json
import uuid

import pytest

from services.attempt_service import attempt_service
from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared sample data + helpers
# ---------------------------------------------------------------------------

_MC_Q = {
    "question_type": "multiple_choice",
    "question_data": {
        "stem": "Capital of France?",
        "options": [{"text": "Paris"}, {"text": "London"}],
        "correct_index": 0,
    },
}

_FILL_Q = {
    "question_type": "fill_blank",
    "question_data": {"correct_answers": ["nine"], "case_sensitive": False},
}


async def _make_simple_exam(make_exam, questions=None):
    """One exam, one section, default 2 questions (MC + fill_blank)."""
    return await make_exam(
        sections=[{"questions": questions or [_MC_Q, _FILL_Q]}],
    )


async def _start_attempt(user_id, exam_id):
    """Start an attempt via the service (Case A). Returns attempt id."""
    result = await attempt_service.start_attempt(user_id=user_id, exam_id=exam_id)
    return result["attempt"]["id"]


async def _fetch_answer_row(db_pool, attempt_id, question_id):
    """Direct SELECT for assertions — returns (student_answer, is_correct,
    points_earned) or None if no row."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT student_answer, is_correct, points_earned
            FROM public.answers
            WHERE attempt_id = $1 AND question_id = $2
            """,
            uuid.UUID(attempt_id),
            uuid.UUID(question_id),
        )
    return row


async def _count_answer_rows(db_pool, attempt_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*)::int FROM public.answers WHERE attempt_id = $1",
            uuid.UUID(attempt_id),
        )


# ===========================================================================
# §8.4 Save answers
# ===========================================================================


async def test_S1_save_new_answers_inserts_rows(make_user, make_exam, db_pool):
    user = await make_user(email="s1@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1, q2 = exam["sections"][0]["questions"]

    result = await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[
            {"questionId": q1["id"], "studentAnswer": 0},
            {"questionId": q2["id"], "studentAnswer": "nine"},
        ],
    )

    assert result["savedCount"] == 2
    assert result["totalQuestions"] == 2
    assert await _count_answer_rows(db_pool, aid) == 2


async def test_S2_save_marks_is_correct_null_and_points_zero(
    make_user, make_exam, db_pool
):
    user = await make_user(email="s2@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1 = exam["sections"][0]["questions"][0]

    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q1["id"], "studentAnswer": 0}],
    )

    row = await _fetch_answer_row(db_pool, aid, q1["id"])
    assert row["is_correct"] is None  # SQL NULL → Python None
    assert row["points_earned"] == 0


async def test_S3_resave_overwrites_student_answer(
    make_user, make_exam, db_pool
):
    user = await make_user(email="s3@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1 = exam["sections"][0]["questions"][0]

    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q1["id"], "studentAnswer": 1}],
    )
    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q1["id"], "studentAnswer": 0}],
    )

    row = await _fetch_answer_row(db_pool, aid, q1["id"])
    # student_answer stored as jsonb → asyncpg returns parsed value or raw
    # string depending on version. Use json.loads to normalize.
    stored = row["student_answer"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored == 0
    # And only one row total — UPSERT didn't insert a duplicate
    assert await _count_answer_rows(db_pool, aid) == 1


async def test_S4_save_partial_does_not_delete_others(
    make_user, make_exam, db_pool
):
    user = await make_user(email="s4@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1, q2 = exam["sections"][0]["questions"]

    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q1["id"], "studentAnswer": 0}],
    )
    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q2["id"], "studentAnswer": "nine"}],
    )

    assert await _count_answer_rows(db_pool, aid) == 2
    # q1 still has the original value
    row1 = await _fetch_answer_row(db_pool, aid, q1["id"])
    stored = row1["student_answer"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored == 0


async def test_S5_save_explicit_null_clears_answer(
    make_user, make_exam, db_pool
):
    user = await make_user(email="s5@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1 = exam["sections"][0]["questions"][0]

    # First save a real answer
    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q1["id"], "studentAnswer": 0}],
    )
    # Then save null
    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q1["id"], "studentAnswer": None}],
    )

    row = await _fetch_answer_row(db_pool, aid, q1["id"])
    # Service: `json.dumps(sa) if sa is not None else None`
    # → student_answer is SQL NULL, not the JSON string "null"
    assert row["student_answer"] is None


async def test_S6_save_empty_list_noop(make_user, make_exam, db_pool):
    user = await make_user(email="s6@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])

    result = await attempt_service.save_answers(
        attempt_id=aid, user_id=user["id"], answers=[]
    )

    assert result["savedCount"] == 0
    assert result["totalQuestions"] == 2
    assert await _count_answer_rows(db_pool, aid) == 0


async def test_S7_save_validates_question_belongs_to_exam(
    make_user, make_exam, db_pool
):
    """Mix valid + foreign question_id → NotFoundError; whole batch rolls back."""
    user = await make_user(email="s7@x.com", password="x")
    exam_a = await _make_simple_exam(make_exam)
    exam_b = await _make_simple_exam(make_exam)  # different exam
    aid = await _start_attempt(user["id"], exam_a["id"])
    own_q = exam_a["sections"][0]["questions"][0]
    foreign_q = exam_b["sections"][0]["questions"][0]

    with pytest.raises(NotFoundError) as exc:
        await attempt_service.save_answers(
            attempt_id=aid,
            user_id=user["id"],
            answers=[
                {"questionId": own_q["id"], "studentAnswer": 0},
                {"questionId": foreign_q["id"], "studentAnswer": 1},
            ],
        )
    # Error message should reference the offending id (first 3 truncated)
    assert foreign_q["id"] in str(exc.value)


async def test_S8_save_rolls_back_on_invalid_question_id(
    make_user, make_exam, db_pool
):
    """After S7's NotFoundError, NO answer rows should exist — full
    transactional rollback, not partial inserts."""
    user = await make_user(email="s8@x.com", password="x")
    exam_a = await _make_simple_exam(make_exam)
    exam_b = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam_a["id"])
    own_q = exam_a["sections"][0]["questions"][0]
    foreign_q = exam_b["sections"][0]["questions"][0]

    with pytest.raises(NotFoundError):
        await attempt_service.save_answers(
            attempt_id=aid,
            user_id=user["id"],
            answers=[
                {"questionId": own_q["id"], "studentAnswer": 0},
                {"questionId": foreign_q["id"], "studentAnswer": 1},
            ],
        )

    assert await _count_answer_rows(db_pool, aid) == 0


async def test_S9_save_does_not_validate_answer_shape(
    make_user, make_exam, db_pool
):
    """Service accepts ANY JSON-serializable studentAnswer at save time —
    shape validation is deferred to submit (grading_utils)."""
    user = await make_user(email="s9@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q_mc = exam["sections"][0]["questions"][0]  # MC question

    # MC expects an int index — sending a string should still SAVE OK.
    await attempt_service.save_answers(
        attempt_id=aid,
        user_id=user["id"],
        answers=[{"questionId": q_mc["id"], "studentAnswer": "weird-string"}],
    )

    row = await _fetch_answer_row(db_pool, aid, q_mc["id"])
    stored = row["student_answer"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored == "weird-string"


async def test_S10_save_403_if_not_owner(make_user, make_exam):
    owner = await make_user(email="s10-owner@x.com", password="x")
    intruder = await make_user(email="s10-intruder@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(owner["id"], exam["id"])
    q1 = exam["sections"][0]["questions"][0]

    with pytest.raises(PermissionDeniedError):
        await attempt_service.save_answers(
            attempt_id=aid,
            user_id=intruder["id"],
            answers=[{"questionId": q1["id"], "studentAnswer": 0}],
        )


async def test_S11_save_400_if_attempt_submitted(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="s11@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    attempt = await make_attempt(user["id"], exam["id"], state="submitted")
    q1 = exam["sections"][0]["questions"][0]

    with pytest.raises(ValidationError) as exc:
        await attempt_service.save_answers(
            attempt_id=attempt["id"],
            user_id=user["id"],
            answers=[{"questionId": q1["id"], "studentAnswer": 0}],
        )
    assert "submitted" in str(exc.value).lower()


async def test_S12_save_400_if_attempt_abandoned(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="s12@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    attempt = await make_attempt(user["id"], exam["id"], state="abandoned")
    q1 = exam["sections"][0]["questions"][0]

    with pytest.raises(ValidationError) as exc:
        await attempt_service.save_answers(
            attempt_id=attempt["id"],
            user_id=user["id"],
            answers=[{"questionId": q1["id"], "studentAnswer": 0}],
        )
    assert "abandoned" in str(exc.value).lower()


async def test_S13_save_404_if_attempt_not_found(make_user):
    user = await make_user(email="s13@x.com", password="x")
    with pytest.raises(NotFoundError):
        await attempt_service.save_answers(
            attempt_id="00000000-0000-0000-0000-000000000000",
            user_id=user["id"],
            answers=[],
        )


async def test_S14_save_for_soft_deleted_question_rejected(
    make_user, make_exam, db_pool
):
    """Soft-deleted question (q.deleted_at IS NOT NULL) is treated as
    missing → NotFoundError, same as a foreign question id."""
    user = await make_user(email="s14@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1 = exam["sections"][0]["questions"][0]

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.questions SET deleted_at = now() WHERE id = $1",
            uuid.UUID(q1["id"]),
        )

    with pytest.raises(NotFoundError):
        await attempt_service.save_answers(
            attempt_id=aid,
            user_id=user["id"],
            answers=[{"questionId": q1["id"], "studentAnswer": 0}],
        )


async def test_S15_save_idempotent_under_retry(
    make_user, make_exam, db_pool
):
    """Sending the same payload twice is a no-op: same DB end-state,
    no duplicate rows (UPSERT semantics)."""
    user = await make_user(email="s15@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    q1 = exam["sections"][0]["questions"][0]

    payload = [{"questionId": q1["id"], "studentAnswer": 0}]
    r1 = await attempt_service.save_answers(
        attempt_id=aid, user_id=user["id"], answers=payload
    )
    r2 = await attempt_service.save_answers(
        attempt_id=aid, user_id=user["id"], answers=payload
    )

    assert r1 == r2
    assert await _count_answer_rows(db_pool, aid) == 1
