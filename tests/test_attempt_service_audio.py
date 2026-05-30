"""Integration tests for attempt_service.record_audio_play (§8.7 ATTEMPT_LIFECYCLE.md).

Service-layer tests; HTTP wiring (POST /api/attempts/{id}/sections/{sid}
/audio-play?materialIndex=N) is covered in §8.11.

Contract recap (verified against services/attempt_service.py:758-878):
  - Input: attempt_id, section_id, material_index: int, user_id
  - Output: camelCase dict — `{materialIndex, audioPlayCount, maxPlays,
    remainingPlays}`. (Note: `remainingPlays` is an EXTRA field beyond
    what ATTEMPT_LIFECYCLE.md §8.7 AU14 documents; tests assert it
    pragmatically so the contract stays explicit.)
  - Validation order:
      1. material_index < 0       → ValidationError
      2. attempt exists           → NotFoundError
      3. owner check              → PermissionDeniedError
      4. not abandoned            → ValidationError
      5. not submitted            → ValidationError
      6. section exists           → NotFoundError
      7. section in this exam     → NotFoundError
      8. material_index in range  → NotFoundError
      9. material is type=audio   → ValidationError  ← NOTE: doc AU11 says
         NotFoundError but the code (line 826) raises ValidationError.
         Tests follow CODE behavior; if the contract is meant to change,
         update ATTEMPT_LIFECYCLE.md AU11 too.
      10. UPSERT increment via jsonb_set (atomic)
      11. If new_count > max_plays → AudioPlayLimitExceededError
          (raise inside the transaction → ROLLBACK undoes the increment)
"""

import asyncio
import json
import uuid

import pytest

from services.attempt_service import (
    AudioPlayLimitExceededError,
    attempt_service,
)
from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Sample data + helpers
# ---------------------------------------------------------------------------

_MC_Q = {
    "question_type": "multiple_choice",
    "question_data": {
        "stem": "?",
        "options": [{"text": "A"}, {"text": "B"}],
        "correct_index": 0,
    },
}


def _audio(url="https://example.com/track.mp3", label="Track"):
    return {"type": "audio", "url": url, "label": label}


async def _make_audio_exam(make_exam, num_audio=1, max_plays=None, extra_materials=None):
    """Create exam with one section containing N audio materials.

    `extra_materials` is prepended to the materials list — useful for
    AU11 (non-audio material in front of audio so material_index 0
    points to text/image).
    """
    audios = [
        _audio(f"https://example.com/track{i}.mp3", label=f"Track {i+1}")
        for i in range(num_audio)
    ]
    materials = (extra_materials or []) + audios
    return await make_exam(
        sections=[{
            "materials": materials,
            "max_audio_plays": max_plays,
            "questions": [_MC_Q],
        }],
    )


async def _start_attempt(user_id, exam_id):
    result = await attempt_service.start_attempt(user_id=user_id, exam_id=exam_id)
    return result["attempt"]["id"]


async def _fetch_audio_counts(db_pool, attempt_id, section_id):
    """Return audio_play_counts JSONB as a dict (or {} if no row)."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT audio_play_counts
            FROM public.attempt_section_state
            WHERE attempt_id = $1 AND section_id = $2
            """,
            uuid.UUID(attempt_id),
            uuid.UUID(section_id),
        )
    if not row:
        return {}
    counts = row["audio_play_counts"]
    if isinstance(counts, str):
        counts = json.loads(counts)
    return counts


# ===========================================================================
# §8.7 Audio play
# ===========================================================================


async def test_AU1_first_play_initializes_counter(
    make_user, make_exam, db_pool
):
    user = await make_user(email="au1@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    result = await attempt_service.record_audio_play(
        attempt_id=aid,
        section_id=sec_id,
        material_index=0,
        user_id=user["id"],
    )

    assert result["audioPlayCount"] == 1
    counts = await _fetch_audio_counts(db_pool, aid, sec_id)
    assert counts == {"0": 1}


async def test_AU2_subsequent_play_increments_counter(
    make_user, make_exam, db_pool
):
    user = await make_user(email="au2@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    # Play twice
    await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )
    await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )
    # Third play
    result = await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )
    assert result["audioPlayCount"] == 3


async def test_AU3_per_material_counters_independent(
    make_user, make_exam, db_pool
):
    user = await make_user(email="au3@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=2)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    # Play idx 0 twice
    await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )
    await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )
    # Play idx 1 once
    r = await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=1, user_id=user["id"]
    )
    assert r["audioPlayCount"] == 1  # idx 1 fresh, not affected by idx 0

    counts = await _fetch_audio_counts(db_pool, aid, sec_id)
    assert counts == {"0": 2, "1": 1}


async def test_AU4_cap_enforced_when_reached(make_user, make_exam, db_pool):
    """max_audio_plays=3 → 3 plays succeed, 4th raises
    AudioPlayLimitExceededError. Counter must NOT advance to 4 (the
    increment's transaction rolls back when the cap check raises)."""
    user = await make_user(email="au4@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1, max_plays=3)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    for _ in range(3):
        await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=0,
            user_id=user["id"],
        )

    with pytest.raises(AudioPlayLimitExceededError):
        await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=0,
            user_id=user["id"],
        )

    counts = await _fetch_audio_counts(db_pool, aid, sec_id)
    assert counts == {"0": 3}  # 4th play rolled back


async def test_AU5_unlimited_when_max_is_null(make_user, make_exam, db_pool):
    """max_audio_plays=NULL → unlimited. Play 20 times (not 100 to save
    seconds in CI) — all succeed."""
    user = await make_user(email="au5@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1, max_plays=None)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    for i in range(20):
        result = await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=0,
            user_id=user["id"],
        )
        assert result["audioPlayCount"] == i + 1
        assert result["maxPlays"] is None


async def test_AU6_400_if_attempt_submitted(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="au6@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    attempt = await make_attempt(user["id"], exam["id"], state="submitted")

    with pytest.raises(ValidationError) as exc:
        await attempt_service.record_audio_play(
            attempt_id=attempt["id"],
            section_id=sec_id,
            material_index=0,
            user_id=user["id"],
        )
    assert "submitted" in str(exc.value).lower()


async def test_AU7_400_if_attempt_abandoned(
    make_user, make_exam, make_attempt
):
    user = await make_user(email="au7@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    attempt = await make_attempt(user["id"], exam["id"], state="abandoned")

    with pytest.raises(ValidationError) as exc:
        await attempt_service.record_audio_play(
            attempt_id=attempt["id"],
            section_id=sec_id,
            material_index=0,
            user_id=user["id"],
        )
    assert "abandoned" in str(exc.value).lower()


async def test_AU8_403_if_not_owner(make_user, make_exam):
    owner = await make_user(email="au8-owner@x.com", password="x")
    intruder = await make_user(email="au8-intruder@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(owner["id"], exam["id"])

    with pytest.raises(PermissionDeniedError):
        await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=0,
            user_id=intruder["id"],
        )


async def test_AU9_404_if_section_not_in_exam(make_user, make_exam):
    """Section belongs to a different exam than the attempt → NotFoundError."""
    user = await make_user(email="au9@x.com", password="x")
    exam_a = await _make_audio_exam(make_exam, num_audio=1)
    exam_b = await _make_audio_exam(make_exam, num_audio=1)
    foreign_sec_id = exam_b["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam_a["id"])

    with pytest.raises(NotFoundError):
        await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=foreign_sec_id,  # ← belongs to exam_b, not exam_a
            material_index=0,
            user_id=user["id"],
        )


async def test_AU10_404_if_material_index_out_of_range(
    make_user, make_exam
):
    user = await make_user(email="au10@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=2)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    with pytest.raises(NotFoundError):
        await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=5,  # section only has 2 audio materials
            user_id=user["id"],
        )


async def test_AU11_validation_error_if_material_index_points_to_non_audio(
    make_user, make_exam
):
    """material at index N has type != "audio" → ValidationError.

    NOTE: ATTEMPT_LIFECYCLE.md §8.7 AU11 says NotFoundError, but the
    service code (line 826) raises ValidationError. Test follows code.
    """
    user = await make_user(email="au11@x.com", password="x")
    # Section: [text(0), audio(1)]
    exam = await _make_audio_exam(
        make_exam,
        num_audio=1,
        extra_materials=[{"type": "text", "content": "Intro to listening"}],
    )
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    with pytest.raises(ValidationError) as exc:
        await attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=0,  # ← text material, not audio
            user_id=user["id"],
        )
    assert "not audio" in str(exc.value).lower() or "audio" in str(exc.value).lower()


async def test_AU12_counter_persists_across_resume(
    make_user, make_exam, db_pool
):
    """Play 2x → resume (Case B, same attempt) → counter still 2 in DB."""
    user = await make_user(email="au12@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )
    await attempt_service.record_audio_play(
        attempt_id=aid, section_id=sec_id, material_index=0, user_id=user["id"]
    )

    # Resume same exam → Case B (same attempt id, no quota tick)
    resume = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    assert resume["is_resume"] is True
    assert resume["attempt"]["id"] == aid

    counts = await _fetch_audio_counts(db_pool, aid, sec_id)
    assert counts == {"0": 2}


async def test_AU13_counter_resets_for_new_attempt_of_same_exam(
    make_user, make_exam
):
    """Play, abandon, start new attempt for same exam → first play on
    the new attempt returns audioPlayCount=1 (fresh attempt_section_state)."""
    user = await make_user(email="au13@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1)
    sec_id = exam["sections"][0]["id"]
    aid1 = await _start_attempt(user["id"], exam["id"])

    # Play once on the first attempt
    await attempt_service.record_audio_play(
        attempt_id=aid1,
        section_id=sec_id,
        material_index=0,
        user_id=user["id"],
    )
    # Abandon and start a new attempt
    await attempt_service.abandon_attempt(attempt_id=aid1, user_id=user["id"])
    aid2_result = await attempt_service.start_attempt(
        user_id=user["id"], exam_id=exam["id"]
    )
    aid2 = aid2_result["attempt"]["id"]
    assert aid2 != aid1  # truly a new attempt

    # First play on the new attempt should be fresh
    result = await attempt_service.record_audio_play(
        attempt_id=aid2,
        section_id=sec_id,
        material_index=0,
        user_id=user["id"],
    )
    assert result["audioPlayCount"] == 1


async def test_AU14_response_returns_count_max_and_extras(
    make_user, make_exam
):
    """Doc AU14 lists `{materialIndex, audioPlayCount, maxPlays}`. Service
    also returns `remainingPlays` — assert it too so the extra field is
    explicit in our contract."""
    user = await make_user(email="au14@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1, max_plays=5)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    result = await attempt_service.record_audio_play(
        attempt_id=aid,
        section_id=sec_id,
        material_index=0,
        user_id=user["id"],
    )

    assert result["materialIndex"] == 0
    assert result["audioPlayCount"] == 1
    assert result["maxPlays"] == 5
    assert result["remainingPlays"] == 4  # 5 - 1


async def test_AU15_concurrent_plays_increment_atomically(
    make_user, make_exam, db_pool
):
    """5 parallel plays → final counter == 5 (no lost updates). The
    UPSERT with `jsonb_set(... + 1)` is atomic per row at the Postgres
    level."""
    user = await make_user(email="au15@x.com", password="x")
    exam = await _make_audio_exam(make_exam, num_audio=1, max_plays=None)
    sec_id = exam["sections"][0]["id"]
    aid = await _start_attempt(user["id"], exam["id"])

    await asyncio.gather(*[
        attempt_service.record_audio_play(
            attempt_id=aid,
            section_id=sec_id,
            material_index=0,
            user_id=user["id"],
        )
        for _ in range(5)
    ])

    counts = await _fetch_audio_counts(db_pool, aid, sec_id)
    assert counts == {"0": 5}
