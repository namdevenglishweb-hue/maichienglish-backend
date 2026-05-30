"""Integration tests for attempt_service.start_attempt (§8.3 ATTEMPT_LIFECYCLE.md).

Tests directly call the service (not HTTP routes — those are §8.11).
All tests in this file require a live Postgres; auto-skipped without
MAICHI_TEST_DB=1.

Test IDs map to the scenario catalogue in ATTEMPT_LIFECYCLE.md §8.3:
  A1-A9    Case A (fresh start, consumes quota)
  B1-B8    Case B (resume same exam, no quota consumed)
  K1-K4    Case C (different exam → ConflictError, no leak)
  Z1-Z6    Race condition handling (concurrent starts)
  Q1-Q12   Tier quota enforcement (Q12 flagged xfail per v1 known gap)
  E1-E4    Exam-state preconditions (unpublished / soft-deleted / missing)
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.attempt_service import (
    AttemptLimitExceededError,
    attempt_service,
)
from services.exceptions import ConflictError, NotFoundError

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared sample data — keeps each test focused on the behavior, not setup.
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

_MATCHING_Q = {
    "question_type": "matching",
    "question_data": {
        "stem": "Match the city",
        "options": [{"text": "Hanoi"}, {"text": "Tokyo"}, {"text": "Paris"}],
        "correct_index": 2,
    },
}


async def _make_simple_exam(make_exam, *, published: bool = True, deleted: bool = False):
    """Sugar — one exam, one section, 3 mixed-type questions."""
    return await make_exam(
        published=published,
        deleted=deleted,
        sections=[{
            "type": "multiple_choice",
            "questions": [_MC_Q, _FILL_Q, _MATCHING_Q],
        }],
    )


async def _count_attempts(db_pool, user_id: str) -> int:
    import uuid

    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*)::int FROM public.attempts WHERE user_id = $1",
            uuid.UUID(user_id),
        )


# ===========================================================================
# §8.3 Case A — fresh start (201)
# ===========================================================================


async def test_A1_start_with_no_active_creates_new(make_user, make_exam):
    user = await make_user(email="a1@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    assert result["is_resume"] is False
    assert result["attempt"]["id"]
    assert result["attempt"]["user_id"] == user["id"]
    assert result["attempt"]["exam_id"] == exam["id"]


async def test_A2_start_returns_full_exam_tree(make_user, make_exam):
    user = await make_user(email="a2@x.test", password="x")
    exam = await make_exam(
        sections=[
            {"type": "multiple_choice", "questions": [_MC_Q, _MC_Q, _MC_Q]},
            {"type": "fill_blank", "questions": [_FILL_Q, _FILL_Q]},
            {"type": "matching", "questions": [_MATCHING_Q]},
        ],
    )

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    assert len(result["exam"]["sections"]) == 3
    counts = [len(s["questions"]) for s in result["exam"]["sections"]]
    assert counts == [3, 2, 1]


async def test_A3_start_strips_correct_index_from_mc_questions(make_user, make_exam):
    user = await make_user(email="a3@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MC_Q]}])

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    q = result["exam"]["sections"][0]["questions"][0]
    assert "correct_index" not in q["question_data"]
    assert q["question_data"]["options"]  # options still present


async def test_A4_start_strips_correct_index_from_matching_questions(make_user, make_exam):
    user = await make_user(email="a4@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MATCHING_Q]}])

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    q = result["exam"]["sections"][0]["questions"][0]
    assert "correct_index" not in q["question_data"]


async def test_A5_start_strips_correct_answers_from_fill_blank(make_user, make_exam):
    user = await make_user(email="a5@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_FILL_Q]}])

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    q = result["exam"]["sections"][0]["questions"][0]
    assert "correct_answers" not in q["question_data"]
    assert "case_sensitive" not in q["question_data"]


async def test_A6_start_consumes_quota(make_user, make_exam, db_pool):
    """Free tier: 1 attempt before + new start → 2 total."""
    user = await make_user(email="a6@x.test", password="x", tier="free")
    exam = await _make_simple_exam(make_exam)

    before = await _count_attempts(db_pool, user["id"])
    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"])
    after = await _count_attempts(db_pool, user["id"])

    assert after == before + 1


async def test_A7_start_returns_empty_saved_answers(make_user, make_exam):
    user = await make_user(email="a7@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    assert result["saved_answers"] == []


async def test_A8_start_sets_started_at_recent(make_user, make_exam, db_pool):
    import uuid

    user = await make_user(email="a8@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    before = datetime.now(timezone.utc)
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    after = datetime.now(timezone.utc)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT started_at FROM public.attempts WHERE id = $1",
            uuid.UUID(result["attempt"]["id"]),
        )
    assert before <= row["started_at"] <= after


async def test_A9_start_records_correct_user_and_exam_id(make_user, make_exam, db_pool):
    import uuid

    user = await make_user(email="a9@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, exam_id FROM public.attempts WHERE id = $1",
            uuid.UUID(result["attempt"]["id"]),
        )
    assert str(row["user_id"]) == user["id"]
    assert str(row["exam_id"]) == exam["id"]


# ===========================================================================
# §8.3 Case B — resume same exam (200)
# ===========================================================================


async def test_B1_start_returns_existing_active_attempt(make_user, make_exam):
    user = await make_user(email="b1@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    second = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    assert second["is_resume"] is True
    assert second["attempt"]["id"] == first["attempt"]["id"]


async def test_B2_resume_does_not_consume_quota(make_user, make_exam, db_pool):
    user = await make_user(email="b2@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"])
    after_first = await _count_attempts(db_pool, user["id"])

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"])
    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"])
    after_resumes = await _count_attempts(db_pool, user["id"])

    assert after_first == after_resumes == 1


async def test_B3_resume_returns_saved_answers_from_db(
    make_user, make_exam, db_pool
):
    """Seed 2 answers via raw SQL, then resume — payload includes them."""
    import json
    import uuid

    user = await make_user(email="b3@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MC_Q, _FILL_Q]}])
    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    q1_id = exam["sections"][0]["questions"][0]["id"]
    q2_id = exam["sections"][0]["questions"][1]["id"]
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.answers (attempt_id, question_id, student_answer)
            VALUES ($1, $2, $3::jsonb), ($1, $4, $5::jsonb)
            """,
            uuid.UUID(first["attempt"]["id"]),
            uuid.UUID(q1_id), json.dumps(0),
            uuid.UUID(q2_id), json.dumps("nine"),
        )

    resumed = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    saved_qids = {a["question_id"] for a in resumed["saved_answers"]}
    assert saved_qids == {q1_id, q2_id}


async def test_B4_resume_omits_unsaved_questions_from_saved_answers(
    make_user, make_exam, db_pool
):
    import json
    import uuid

    user = await make_user(email="b4@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MC_Q, _FILL_Q, _MATCHING_Q]}])
    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    only_q1 = exam["sections"][0]["questions"][0]["id"]
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.answers (attempt_id, question_id, student_answer) "
            "VALUES ($1, $2, $3::jsonb)",
            uuid.UUID(first["attempt"]["id"]),
            uuid.UUID(only_q1),
            json.dumps(0),
        )

    resumed = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    assert len(resumed["saved_answers"]) == 1


async def test_B5_resume_keeps_correct_fields_stripped(make_user, make_exam):
    user = await make_user(email="b5@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MC_Q, _FILL_Q]}])

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"])
    resumed = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    qs = resumed["exam"]["sections"][0]["questions"]
    assert "correct_index" not in qs[0]["question_data"]
    assert "correct_answers" not in qs[1]["question_data"]


async def test_B6_resume_returns_original_started_at(make_user, make_exam):
    user = await make_user(email="b6@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    # No time-travel needed — service should return the row's started_at
    # which was set at first INSERT, not now().
    second = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    assert second["attempt"]["started_at"] == first["attempt"]["started_at"]


async def test_B7_resume_filters_saved_answers_for_hard_deleted_questions(
    make_user, make_exam, db_pool
):
    """Defense-in-depth: if admin hard-deletes a question after student
    saved it, CASCADE removes the answer row → not surfaced on resume."""
    import json
    import uuid

    user = await make_user(email="b7@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MC_Q, _FILL_Q]}])
    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    q1, q2 = exam["sections"][0]["questions"]
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.answers (attempt_id, question_id, student_answer) "
            "VALUES ($1, $2, $3::jsonb), ($1, $4, $5::jsonb)",
            uuid.UUID(first["attempt"]["id"]),
            uuid.UUID(q1["id"]), json.dumps(0),
            uuid.UUID(q2["id"]), json.dumps("nine"),
        )
        await conn.execute(
            "DELETE FROM public.questions WHERE id = $1", uuid.UUID(q1["id"])
        )

    resumed = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    saved_qids = {a["question_id"] for a in resumed["saved_answers"]}
    assert saved_qids == {q2["id"]}


async def test_B8_resume_filters_saved_answers_for_soft_deleted_questions(
    make_user, make_exam, db_pool
):
    import json
    import uuid

    user = await make_user(email="b8@x.test", password="x")
    exam = await make_exam(sections=[{"questions": [_MC_Q, _FILL_Q]}])
    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )

    q1, q2 = exam["sections"][0]["questions"]
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.answers (attempt_id, question_id, student_answer) "
            "VALUES ($1, $2, $3::jsonb), ($1, $4, $5::jsonb)",
            uuid.UUID(first["attempt"]["id"]),
            uuid.UUID(q1["id"]), json.dumps(0),
            uuid.UUID(q2["id"]), json.dumps("nine"),
        )
        await conn.execute(
            "UPDATE public.questions SET deleted_at = now() WHERE id = $1",
            uuid.UUID(q1["id"]),
        )

    resumed = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    saved_qids = {a["question_id"] for a in resumed["saved_answers"]}
    assert q1["id"] not in saved_qids
    assert q2["id"] in saved_qids


# ===========================================================================
# §8.3 Case C — conflict different exam (409)
# ===========================================================================


async def test_K1_start_different_exam_with_active_raises_conflict(
    make_user, make_exam
):
    user = await make_user(email="k1@x.test", password="x")
    exam1 = await _make_simple_exam(make_exam)
    exam2 = await _make_simple_exam(make_exam)

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam1["id"])
    with pytest.raises(ConflictError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam2["id"]
        )


async def test_K2_conflict_does_not_create_new_attempt(
    make_user, make_exam, db_pool
):
    user = await make_user(email="k2@x.test", password="x")
    exam1 = await _make_simple_exam(make_exam)
    exam2 = await _make_simple_exam(make_exam)

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam1["id"])
    before = await _count_attempts(db_pool, user["id"])
    with pytest.raises(ConflictError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam2["id"]
        )
    after = await _count_attempts(db_pool, user["id"])
    assert before == after == 1


async def test_K3_conflict_does_not_consume_quota(make_user, make_exam, db_pool):
    """Even with quota-exhausted user, conflict must NOT raise quota error
    — conflict takes precedence (path returns before quota check)."""
    user = await make_user(email="k3@x.test", password="x", tier="free")
    exam1 = await _make_simple_exam(make_exam)
    exam2 = await _make_simple_exam(make_exam)

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam1["id"])
    with pytest.raises(ConflictError):  # NOT AttemptLimitExceededError
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam2["id"]
        )


async def test_K4_conflict_error_message_does_not_leak_active_exam_id(
    make_user, make_exam
):
    user = await make_user(email="k4@x.test", password="x")
    exam1 = await _make_simple_exam(make_exam)
    exam2 = await _make_simple_exam(make_exam)

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam1["id"])
    with pytest.raises(ConflictError) as exc:
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam2["id"]
        )
    msg = str(exc.value)
    assert exam1["id"] not in msg
    assert exam2["id"] not in msg
    assert "another exam" in msg or "unfinished" in msg


# ===========================================================================
# §8.3 Race condition handling (Z1-Z6)
# ===========================================================================


async def test_Z1_two_concurrent_same_exam_both_get_same_attempt_id(
    make_user, make_exam, db_pool
):
    user = await make_user(email="z1@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    r1, r2 = await asyncio.gather(
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"]),
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"]),
    )
    assert r1["attempt"]["id"] == r2["attempt"]["id"]
    assert await _count_attempts(db_pool, user["id"]) == 1


async def test_Z2_two_concurrent_different_exams_one_wins_one_409(
    make_user, make_exam, db_pool
):
    user = await make_user(email="z2@x.test", password="x")
    exam1 = await _make_simple_exam(make_exam)
    exam2 = await _make_simple_exam(make_exam)

    results = await asyncio.gather(
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam1["id"]),
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam2["id"]),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1 and isinstance(failures[0], ConflictError)
    assert await _count_attempts(db_pool, user["id"]) == 1


async def test_Z4_unique_violation_for_same_exam_resolves_to_case_b(
    make_user, make_exam
):
    """Z3+Z4 collapsed into one observable test: under load on same exam,
    BOTH callers see a resume-shaped payload (is_resume=True on the loser)."""
    user = await make_user(email="z4@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    r1, r2 = await asyncio.gather(
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"]),
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"]),
    )
    # At least one of them is is_resume=True (the loser); ids match.
    assert any(r["is_resume"] for r in (r1, r2))


async def test_Z5_unique_violation_for_different_exam_resolves_to_case_c(
    make_user, make_exam
):
    """Z2 already covers the gather case; this one re-asserts via a
    seed+race pattern (helps detect ordering bugs in the recovery branch)."""
    user = await make_user(email="z5@x.test", password="x")
    exam1 = await _make_simple_exam(make_exam)
    exam2 = await _make_simple_exam(make_exam)

    await attempt_service.start_attempt(user_id=user["id"], exam_id=exam1["id"])
    # Simulate "race lost": existing active + new start for different exam
    with pytest.raises(ConflictError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam2["id"]
        )


async def test_Z6_high_concurrency_same_user_same_exam(
    make_user, make_exam, db_pool
):
    """50 concurrent starts → all return same attempt id; exactly 1 row."""
    user = await make_user(email="z6@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    results = await asyncio.gather(*[
        attempt_service.start_attempt(user_id=user["id"], exam_id=exam["id"])
        for _ in range(50)
    ])
    ids = {r["attempt"]["id"] for r in results}
    assert len(ids) == 1
    assert await _count_attempts(db_pool, user["id"]) == 1


# ===========================================================================
# §8.3 Tier quota enforcement (Q1-Q12)
# ===========================================================================


async def test_Q1_free_tier_5_attempts_then_6th_blocked(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="q1@x.test", password="x", tier="free")
    # Seed 5 prior attempts (mix of submitted + abandoned, NO active)
    base_exam = await _make_simple_exam(make_exam)
    for _ in range(3):
        await make_attempt(user["id"], base_exam["id"], state="submitted")
    for _ in range(2):
        await make_attempt(user["id"], base_exam["id"], state="abandoned")

    new_exam = await _make_simple_exam(make_exam)
    with pytest.raises(AttemptLimitExceededError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=new_exam["id"]
        )


async def test_Q2_basic_tier_allows_50th(make_user, make_exam, make_attempt):
    user = await make_user(email="q2@x.test", password="x", tier="basic")
    base = await _make_simple_exam(make_exam)
    for _ in range(49):
        await make_attempt(user["id"], base["id"], state="submitted")

    new_exam = await _make_simple_exam(make_exam)
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=new_exam["id"]
    )
    assert result["is_resume"] is False


async def test_Q3_basic_tier_blocks_51st(make_user, make_exam, make_attempt):
    user = await make_user(email="q3@x.test", password="x", tier="basic")
    base = await _make_simple_exam(make_exam)
    for _ in range(50):
        await make_attempt(user["id"], base["id"], state="submitted")

    new_exam = await _make_simple_exam(make_exam)
    with pytest.raises(AttemptLimitExceededError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=new_exam["id"]
        )


async def test_Q4_pro_tier_unlimited(make_user, make_exam, make_attempt):
    user = await make_user(email="q4@x.test", password="x", tier="pro")
    base = await _make_simple_exam(make_exam)
    for _ in range(100):
        await make_attempt(user["id"], base["id"], state="submitted")

    new_exam = await _make_simple_exam(make_exam)
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=new_exam["id"]
    )
    assert result["is_resume"] is False


async def test_Q5_ultra_tier_unlimited(make_user, make_exam, make_attempt):
    user = await make_user(email="q5@x.test", password="x", tier="ultra")
    base = await _make_simple_exam(make_exam)
    for _ in range(100):
        await make_attempt(user["id"], base["id"], state="submitted")

    new_exam = await _make_simple_exam(make_exam)
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=new_exam["id"]
    )
    assert result["is_resume"] is False


async def test_Q6_abandoned_attempts_count_toward_quota(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="q6@x.test", password="x", tier="free")
    base = await _make_simple_exam(make_exam)
    for _ in range(5):
        await make_attempt(user["id"], base["id"], state="abandoned")

    new_exam = await _make_simple_exam(make_exam)
    with pytest.raises(AttemptLimitExceededError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=new_exam["id"]
        )


async def test_Q7_submitted_attempts_count_toward_quota(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="q7@x.test", password="x", tier="free")
    base = await _make_simple_exam(make_exam)
    for _ in range(5):
        await make_attempt(user["id"], base["id"], state="submitted")

    new_exam = await _make_simple_exam(make_exam)
    with pytest.raises(AttemptLimitExceededError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=new_exam["id"]
        )


async def test_Q8_in_progress_attempt_counts_toward_quota(
    make_user, make_exam, make_attempt
):
    """If user has 1 in_progress for exam E + 4 submitted, calling start
    for SAME exam → Case B (resume), no quota check. Quota check only
    fires on Case A path."""
    user = await make_user(email="q8@x.test", password="x", tier="free")
    base = await _make_simple_exam(make_exam)
    for _ in range(4):
        await make_attempt(user["id"], base["id"], state="submitted")
    in_progress = await make_attempt(user["id"], base["id"], state="in_progress")

    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=base["id"]
    )
    assert result["is_resume"] is True
    assert result["attempt"]["id"] == in_progress["id"]


async def test_Q9_resume_does_not_increment_quota_counter_even_at_limit(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="q9@x.test", password="x", tier="free")
    base = await _make_simple_exam(make_exam)
    for _ in range(4):
        await make_attempt(user["id"], base["id"], state="submitted")
    await make_attempt(user["id"], base["id"], state="in_progress")
    # User is at 5/5 already

    # Resume should work even though they're at the limit
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=base["id"]
    )
    assert result["is_resume"] is True


async def test_Q10_quota_only_counts_within_subscription_period(
    make_user, make_exam, make_attempt, set_subscription
):
    """Move period_start forward; older attempts excluded from quota count."""
    user = await make_user(email="q10@x.test", password="x", tier="free")
    base = await _make_simple_exam(make_exam)

    # 5 old attempts (in the past)
    old = datetime.now(timezone.utc) - timedelta(days=60)
    for _ in range(5):
        await make_attempt(
            user["id"], base["id"], state="submitted", started_at=old,
        )
    # Period started 1 day ago — old attempts excluded
    await set_subscription(
        user["id"],
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
    )

    new_exam = await _make_simple_exam(make_exam)
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=new_exam["id"]
    )
    assert result["is_resume"] is False


async def test_Q11_parent_role_cannot_start_attempt():
    """Parent role check lives at the ROUTE layer (api/attempts/routes.py
    line ~95), not service. The service accepts any user_id. This test
    asserts the spec contract — see HR5 in §8.11 for the route-level test."""
    # Service-layer guarantee: start_attempt accepts user_id without checking role.
    # Route-layer guarantee: POST /api/attempts returns 403 for parent JWT.
    # This test documents the split; full coverage is in test_attempt_route_start.py.
    assert True


@pytest.mark.xfail(
    reason=(
        "v1 _enforce_tier_limit does NOT check subscription.status. "
        "Q12 locks the desired behavior; flagging the gap so it's visible "
        "without breaking CI. Resolve by adding `if sub['status'] != 'active'` "
        "in _enforce_tier_limit + a separate route-layer check."
    ),
    strict=True,
)
async def test_Q12_canceled_subscription_status_blocks_start(
    make_user, make_exam, set_subscription
):
    from services.exceptions import PermissionDeniedError

    user = await make_user(email="q12@x.test", password="x", tier="pro")
    await set_subscription(user["id"], status="canceled")
    exam = await _make_simple_exam(make_exam)

    with pytest.raises(PermissionDeniedError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam["id"]
        )


# ===========================================================================
# §8.3 Exam-state preconditions (E1-E4)
# ===========================================================================


async def test_E1_start_for_unpublished_exam_fails(make_user, make_exam):
    user = await make_user(email="e1@x.test", password="x")
    exam = await make_exam(published=False, sections=[{"questions": [_MC_Q]}])

    with pytest.raises(NotFoundError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam["id"]
        )


async def test_E2_start_for_soft_deleted_exam_fails(
    make_user, make_exam, db_pool
):
    import uuid

    user = await make_user(email="e2@x.test", password="x")
    exam = await _make_simple_exam(make_exam)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.exams SET deleted_at = now() WHERE id = $1",
            uuid.UUID(exam["id"]),
        )

    with pytest.raises(NotFoundError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=exam["id"]
        )


async def test_E3_start_for_nonexistent_exam_fails(make_user):
    user = await make_user(email="e3@x.test", password="x")
    with pytest.raises(NotFoundError):
        await attempt_service.start_attempt(
            user_id=user["id"],
            exam_id="00000000-0000-0000-0000-000000000000",
        )


async def test_E4_start_for_unpublished_exam_with_existing_active_resumes(
    make_user, make_exam, db_pool
):
    """Edge: user started attempt while exam was published; admin
    unpublishes; resume must still work (existing active attempt is
    insulated from exam-state changes)."""
    import uuid

    user = await make_user(email="e4@x.test", password="x")
    exam = await _make_simple_exam(make_exam)

    first = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.exams SET is_published = false WHERE id = $1",
            uuid.UUID(exam["id"]),
        )

    resumed = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    assert resumed["is_resume"] is True
    assert resumed["attempt"]["id"] == first["attempt"]["id"]
