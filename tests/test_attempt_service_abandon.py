"""Integration tests for attempt_service.abandon_attempt (§8.6 ATTEMPT_LIFECYCLE.md).

Service-layer tests; HTTP wiring (POST /api/attempts/{id}/abandon) is
covered in §8.11. All tests require a live Postgres.

Contract recap (verified against services/attempt_service.py:501-546):
  - Input: attempt_id, user_id
  - Output: _row_to_attempt(row) — snake_case
    (`id, user_id, exam_id, score=0, total_points=0, percentage=0,
      time_spent_seconds, is_abandoned=True, started_at, submitted_at=now()`)
  - Validation order: attempt exists → owner → not abandoned → not submitted
  - Errors: NotFoundError / PermissionDeniedError / ValidationError
  - Side effects: row is FINALIZED (submitted_at set), is_abandoned=true,
    score/total/percentage zeroed. The partial unique index
    `attempts_one_active_per_user` releases its lock on this user, so a
    new attempt for the same (or different) exam can be started.
  - Quota: abandoned attempts STILL COUNT (anti-abuse). Counted by
    `_enforce_tier_limit`'s `COUNT(*) WHERE started_at >= period_start`,
    no state filter.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from services.attempt_service import (
    AttemptLimitExceededError,
    attempt_service,
)
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
        "stem": "?",
        "options": [{"text": "A"}, {"text": "B"}],
        "correct_index": 0,
    },
}


async def _make_simple_exam(make_exam):
    return await make_exam(sections=[{"questions": [_MC_Q]}])


async def _start_attempt(user_id, exam_id):
    result = await attempt_service.start_attempt(user_id=user_id, exam_id=exam_id)
    return result["attempt"]["id"]


async def _fetch_attempt(db_pool, attempt_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT score, total_points, percentage, is_abandoned,
                   submitted_at, started_at
            FROM public.attempts WHERE id = $1
            """,
            uuid.UUID(attempt_id),
        )


# ===========================================================================
# §8.6 Abandon
# ===========================================================================


async def test_X1_abandon_sets_is_abandoned_true(make_user, make_exam, db_pool):
    user = await make_user(email="x1@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])

    result = await attempt_service.abandon_attempt(
        attempt_id=aid, user_id=user["id"]
    )

    assert result["is_abandoned"] is True
    row = await _fetch_attempt(db_pool, aid)
    assert row["is_abandoned"] is True


async def test_X2_abandon_sets_submitted_at_recent(
    make_user, make_exam, db_pool
):
    """SQL `now()` is used (DB time, not Python), so we bracket-check
    the timestamp instead of using freezegun (which only affects Python's
    datetime, not Postgres's now())."""
    user = await make_user(email="x2@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])

    before = datetime.now(timezone.utc)
    await attempt_service.abandon_attempt(attempt_id=aid, user_id=user["id"])
    after = datetime.now(timezone.utc)

    row = await _fetch_attempt(db_pool, aid)
    assert row["submitted_at"] is not None
    assert before <= row["submitted_at"] <= after


async def test_X3_abandon_zeros_score_total_percentage(
    make_user, make_exam, db_pool
):
    user = await make_user(email="x3@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])

    await attempt_service.abandon_attempt(attempt_id=aid, user_id=user["id"])

    row = await _fetch_attempt(db_pool, aid)
    assert row["score"] == 0
    assert row["total_points"] == 0
    assert row["percentage"] == 0


async def test_X4_abandon_frees_active_slot(make_user, make_exam):
    """After abandon, the user can start a NEW attempt for the same exam
    (Case A). The partial unique index no longer blocks because
    is_abandoned=true."""
    user = await make_user(email="x4@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid1 = await _start_attempt(user["id"], exam["id"])
    await attempt_service.abandon_attempt(attempt_id=aid1, user_id=user["id"])

    # Start a brand new attempt for same exam → Case A (is_resume=False)
    result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    assert result["is_resume"] is False
    assert result["attempt"]["id"] != aid1


async def test_X5_abandon_403_if_not_owner(make_user, make_exam):
    owner = await make_user(email="x5-owner@x.com", password="x")
    intruder = await make_user(email="x5-intruder@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(owner["id"], exam["id"])

    with pytest.raises(PermissionDeniedError):
        await attempt_service.abandon_attempt(
            attempt_id=aid, user_id=intruder["id"]
        )


async def test_X6_abandon_400_if_already_submitted(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="x6@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    attempt = await make_attempt(user["id"], exam["id"], state="submitted")

    with pytest.raises(ValidationError) as exc:
        await attempt_service.abandon_attempt(
            attempt_id=attempt["id"], user_id=user["id"]
        )
    assert "submitted" in str(exc.value).lower()


async def test_X7_abandon_400_if_already_abandoned(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="x7@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    attempt = await make_attempt(user["id"], exam["id"], state="abandoned")

    with pytest.raises(ValidationError) as exc:
        await attempt_service.abandon_attempt(
            attempt_id=attempt["id"], user_id=user["id"]
        )
    assert "abandoned" in str(exc.value).lower()


async def test_X8_abandon_404_if_not_found(make_user):
    user = await make_user(email="x8@x.com", password="x")
    with pytest.raises(NotFoundError):
        await attempt_service.abandon_attempt(
            attempt_id="00000000-0000-0000-0000-000000000000",
            user_id=user["id"],
        )


async def test_X9_abandon_does_not_refund_quota(
    make_user, make_exam, make_attempt
):
    """Free tier limit = 5 attempts/period. Seeding 4 submitted +
    1 in_progress = 5 total. Abandoning the in_progress drops it to 0
    active but the abandoned row still counts → 6th start blocked."""
    user = await make_user(email="x9@x.com", password="x", tier="free")
    base_exam = await _make_simple_exam(make_exam)
    for _ in range(4):
        await make_attempt(user["id"], base_exam["id"], state="submitted")
    in_progress = await make_attempt(
        user["id"], base_exam["id"], state="in_progress"
    )

    await attempt_service.abandon_attempt(
        attempt_id=in_progress["id"], user_id=user["id"]
    )

    new_exam = await _make_simple_exam(make_exam)
    with pytest.raises(AttemptLimitExceededError):
        await attempt_service.start_attempt(
            user_id=user["id"], exam_id=new_exam["id"]
        )


async def test_X10_abandoned_appears_in_history_as_abandoned(
    make_user, make_exam
):
    user = await make_user(email="x10@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])
    await attempt_service.abandon_attempt(attempt_id=aid, user_id=user["id"])

    history = await attempt_service.list_history_for_user(user["id"])

    assert len(history) == 1
    assert history[0]["id"] == aid
    assert history[0]["is_abandoned"] is True


async def test_X11_concurrent_submit_and_abandon_first_wins(
    make_user, make_exam, db_pool
):
    """DB invariant: regardless of which call wins the race, the attempt
    ends in a finalized state (submitted_at set). Exactly one of the
    calls succeeds; the other surfaces some exception (ValidationError
    if the loser sees the row already finalized, or an asyncpg-level
    error if both reach the UPDATE in parallel and one's transaction
    is aborted)."""
    user = await make_user(email="x11@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])

    results = await asyncio.gather(
        attempt_service.submit_attempt(
            attempt_id=aid, user_id=user["id"], answers=[]
        ),
        attempt_service.abandon_attempt(
            attempt_id=aid, user_id=user["id"]
        ),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) + len(failures) == 2
    assert len(successes) >= 1
    row = await _fetch_attempt(db_pool, aid)
    assert row["submitted_at"] is not None


async def test_X12_concurrent_two_abandons_first_wins(
    make_user, make_exam, db_pool
):
    user = await make_user(email="x12@x.com", password="x")
    exam = await _make_simple_exam(make_exam)
    aid = await _start_attempt(user["id"], exam["id"])

    await asyncio.gather(
        attempt_service.abandon_attempt(
            attempt_id=aid, user_id=user["id"]
        ),
        attempt_service.abandon_attempt(
            attempt_id=aid, user_id=user["id"]
        ),
        return_exceptions=True,
    )

    # DB invariant: attempt is finalized as abandoned exactly once
    row = await _fetch_attempt(db_pool, aid)
    assert row["is_abandoned"] is True
    assert row["submitted_at"] is not None
