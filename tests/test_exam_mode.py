"""Exam mode (thi thử / thi thật) — MD/NR/AU/PL/BC.

Integration tests; auto-skipped unless the integration DB is enabled.
Covers docs/exam-mode/exam-mode-testcases.md.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _student(auth_headers, user):
    return auth_headers(user["email"], role="student")


async def _start(client, headers, exam_id, mode=None):
    body = {"examId": exam_id}
    if mode is not None:
        body["mode"] = mode
    return await client.post("/api/attempts", headers=headers, json=body)


_AUDIO_SECTION = {
    "type": "multiple_choice",
    "materials": [{"type": "audio", "url": "http://x/a.mp3", "label": "A"}],
    "max_audio_plays": 3,
    "questions": [
        {
            "question_type": "multiple_choice",
            "question_data": {
                "stem": "q1",
                "options": [{"text": "a"}, {"text": "b"}],
                "correct_index": 0,
            },
        }
    ],
}


# ===================================================================== #
# Start — chọn mode (MD)                                               #
# ===================================================================== #


async def test_start_default_mode_is_practice(
    client, auth_headers, make_user, make_exam
):
    """MD3 — start không gửi mode → practice."""
    s = await make_user(email="md3@x.com", role="student")
    exam = await make_exam()
    r = await _start(client, _student(auth_headers, s), exam["id"])
    assert r.status_code == 201
    act = await client.get("/api/attempts/active", headers=_student(auth_headers, s))
    assert act.json()["data"]["mode"] == "practice"


async def test_start_real_sets_mode_real(
    client, auth_headers, make_user, make_exam
):
    """MD4 — start mode=real → attempt real."""
    s = await make_user(email="md4@x.com", role="student")
    exam = await make_exam()
    r = await _start(client, _student(auth_headers, s), exam["id"], "real")
    assert r.status_code == 201
    act = await client.get("/api/attempts/active", headers=_student(auth_headers, s))
    assert act.json()["data"]["mode"] == "real"


async def test_start_invalid_mode_422(
    client, auth_headers, make_user, make_exam
):
    """MD5 — mode sai → 422."""
    s = await make_user(email="md5@x.com", role="student")
    exam = await make_exam()
    r = await _start(client, _student(auth_headers, s), exam["id"], "real-exam")
    assert r.status_code == 422


# ===================================================================== #
# No-resume (NR)                                                       #
# ===================================================================== #


async def test_real_active_then_start_abandons_old(
    client, auth_headers, make_user, make_exam, db_pool
):
    """NR1 — real active → start lại → real cũ abandon, attempt mới được tạo."""
    s = await make_user(email="nr1@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    r1 = await _start(client, h, exam["id"], "real")
    old_id = r1.json()["data"]["attemptId"]

    r2 = await _start(client, h, exam["id"], "real")
    assert r2.status_code == 201
    new_id = r2.json()["data"]["attemptId"]
    assert new_id != old_id

    async with db_pool.acquire() as conn:
        old = await conn.fetchrow(
            "SELECT is_abandoned, score FROM public.attempts WHERE id=$1",
            uuid.UUID(old_id),
        )
    assert old["is_abandoned"] is True
    assert old["score"] == 0


async def test_start_real_when_practice_active_same_exam_409(
    client, auth_headers, make_user, make_exam
):
    """NR4b — practice dở cùng đề + start real → 409 (không nuốt thành resume)."""
    s = await make_user(email="nr4b@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    await _start(client, h, exam["id"], "practice")
    r = await _start(client, h, exam["id"], "real")
    assert r.status_code == 409


async def test_start_practice_same_exam_resumes(
    client, auth_headers, make_user, make_exam
):
    """MD6 — practice dở cùng đề + practice → resume (200)."""
    s = await make_user(email="md6@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    r1 = await _start(client, h, exam["id"], "practice")
    assert r1.status_code == 201
    r2 = await _start(client, h, exam["id"], "practice")
    assert r2.status_code == 200
    assert r2.json()["data"]["isResume"] is True


# ===================================================================== #
# Audio cap (AU)                                                       #
# ===================================================================== #


async def _play(client, headers, attempt_id, section_id, idx=0):
    return await client.post(
        f"/api/attempts/{attempt_id}/sections/{section_id}/audio-play"
        f"?materialIndex={idx}",
        headers=headers,
    )


async def test_real_audio_second_play_rejected(
    client, auth_headers, make_user, make_exam
):
    """AU1 — real: nghe lần 2 cùng audio → 403."""
    s = await make_user(email="au1@x.com", role="student")
    exam = await make_exam(sections=[_AUDIO_SECTION])
    sec_id = exam["sections"][0]["id"]
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"], "real")).json()["data"]["attemptId"]
    assert (await _play(client, h, att, sec_id)).status_code == 200
    assert (await _play(client, h, att, sec_id)).status_code == 403


async def test_real_overrides_section_cap(
    client, auth_headers, make_user, make_exam
):
    """AU2 — section cap=3 nhưng real → vẫn chỉ 1 lần (lần 2 chặn)."""
    s = await make_user(email="au2@x.com", role="student")
    exam = await make_exam(sections=[_AUDIO_SECTION])  # max_audio_plays=3
    sec_id = exam["sections"][0]["id"]
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"], "real")).json()["data"]["attemptId"]
    assert (await _play(client, h, att, sec_id)).status_code == 200
    assert (await _play(client, h, att, sec_id)).status_code == 403


async def test_practice_uses_section_cap(
    client, auth_headers, make_user, make_exam
):
    """AU4 — practice + cap=3 → 3 lần OK, lần 4 chặn."""
    s = await make_user(email="au4@x.com", role="student")
    exam = await make_exam(sections=[_AUDIO_SECTION])  # max_audio_plays=3
    sec_id = exam["sections"][0]["id"]
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"], "practice")).json()["data"]["attemptId"]
    for _ in range(3):
        assert (await _play(client, h, att, sec_id)).status_code == 200
    assert (await _play(client, h, att, sec_id)).status_code == 403


# ===================================================================== #
# Payload (PL)                                                         #
# ===================================================================== #


async def test_real_start_payload_max_audio_plays_one(
    client, auth_headers, make_user, make_exam
):
    """PL1 — start real → mỗi section maxAudioPlays=1."""
    s = await make_user(email="pl1@x.com", role="student")
    exam = await make_exam(sections=[_AUDIO_SECTION])  # configured 3
    h = _student(auth_headers, s)
    r = await _start(client, h, exam["id"], "real")
    secs = r.json()["data"]["exam"]["sections"]
    assert all(sec["maxAudioPlays"] == 1 for sec in secs)


async def test_practice_start_payload_keeps_section_cap(
    client, auth_headers, make_user, make_exam
):
    """PL2 — start practice → maxAudioPlays = cấu hình section (3)."""
    s = await make_user(email="pl2@x.com", role="student")
    exam = await make_exam(sections=[_AUDIO_SECTION])
    h = _student(auth_headers, s)
    r = await _start(client, h, exam["id"], "practice")
    secs = r.json()["data"]["exam"]["sections"]
    assert secs[0]["maxAudioPlays"] == 3


async def test_mode_surfaced_on_active_and_history(
    client, auth_headers, make_user, make_exam
):
    """PL3 — mode xuất hiện ở /active + history."""
    s = await make_user(email="pl3@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    await _start(client, h, exam["id"], "real")
    act = await client.get("/api/attempts/active", headers=h)
    assert act.json()["data"]["mode"] == "real"
    hist = await client.get("/api/attempts/history", headers=h)
    assert hist.json()["data"]["items"][0]["mode"] == "real"
