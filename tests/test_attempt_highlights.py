"""Attempt highlights — HC/HE/HD/HL/HR/HX.

Integration tests; auto-skipped unless the integration DB is enabled.
Covers docs/attempt-highlights/attempt-highlights-testcases.md.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _student(auth_headers, user):
    return auth_headers(user["email"], role="student")


def _teacher(auth_headers, user):
    return auth_headers(user["email"], role="teacher")


async def _start(client, headers, exam_id):
    return await client.post(
        "/api/attempts", headers=headers, json={"examId": exam_id}
    )


async def _mk_hl(client, headers, attempt_id, **kw):
    body = {
        "targetKey": "question:q1:stem",
        "rangeStart": 0,
        "rangeEnd": 5,
        "quotedText": "Hello",
        **kw,
    }
    return await client.post(
        f"/api/attempts/{attempt_id}/highlights", headers=headers, json=body
    )


async def _mark_submitted(db_pool, attempt_id):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.attempts SET submitted_at = now() WHERE id = $1",
            uuid.UUID(attempt_id),
        )


# ===================================================================== #
# Create (HC)                                                          #
# ===================================================================== #


async def test_create_highlight_owner_in_progress_201(
    client, auth_headers, make_user, make_exam
):
    """HC1"""
    s = await make_user(email="hc1@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    r = await _mk_hl(client, h, att)
    assert r.status_code == 201
    hl = r.json()["data"]["highlight"]
    assert hl["targetKey"] == "question:q1:stem"
    assert hl["rangeStart"] == 0 and hl["rangeEnd"] == 5
    assert hl["id"]


async def test_create_with_note_and_color(
    client, auth_headers, make_user, make_exam
):
    """HC2"""
    s = await make_user(email="hc2@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    r = await _mk_hl(client, h, att, note="nhớ chỗ này", color="yellow")
    assert r.status_code == 201
    hl = r.json()["data"]["highlight"]
    assert hl["note"] == "nhớ chỗ này"
    assert hl["color"] == "yellow"


async def test_create_overlapping_allowed(
    client, auth_headers, make_user, make_exam
):
    """HC4 — two highlights on the same range both succeed (no overlap rule)."""
    s = await make_user(email="hc4@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    assert (await _mk_hl(client, h, att)).status_code == 201
    assert (await _mk_hl(client, h, att)).status_code == 201


async def test_create_non_owner_403(
    client, auth_headers, make_user, make_exam
):
    """HC6"""
    owner = await make_user(email="hc6-o@x.com", role="student")
    other = await make_user(email="hc6-x@x.com", role="student")
    exam = await make_exam()
    att = (await _start(client, _student(auth_headers, owner), exam["id"])).json()[
        "data"
    ]["attemptId"]
    r = await _mk_hl(client, _student(auth_headers, other), att)
    assert r.status_code == 403


async def test_create_after_submit_blocked(
    client, auth_headers, make_user, make_exam, db_pool
):
    """HC7 — not in_progress → 400."""
    s = await make_user(email="hc7@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    await _mark_submitted(db_pool, att)
    r = await _mk_hl(client, h, att)
    assert r.status_code == 400


async def test_create_invalid_range_422(
    client, auth_headers, make_user, make_exam
):
    """HC9 — rangeEnd <= rangeStart → 422."""
    s = await make_user(email="hc9@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    r = await _mk_hl(client, h, att, rangeStart=5, rangeEnd=5)
    assert r.status_code == 422


# ===================================================================== #
# Edit / Delete (HE / HD)                                              #
# ===================================================================== #


async def test_patch_note_owner_in_progress_200(
    client, auth_headers, make_user, make_exam
):
    """HE1"""
    s = await make_user(email="he1@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    hl_id = (await _mk_hl(client, h, att)).json()["data"]["highlight"]["id"]
    r = await client.patch(
        f"/api/attempts/{att}/highlights/{hl_id}",
        headers=h,
        json={"note": "updated"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["highlight"]["note"] == "updated"


async def test_delete_owner_204_keeps_overlapping(
    client, auth_headers, make_user, make_exam
):
    """HD1 + HD2 — delete one of two overlapping; the other remains."""
    s = await make_user(email="hd1@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    a_id = (await _mk_hl(client, h, att)).json()["data"]["highlight"]["id"]
    await _mk_hl(client, h, att)  # second, overlapping

    r = await client.delete(
        f"/api/attempts/{att}/highlights/{a_id}", headers=h
    )
    assert r.status_code == 204

    detail = await client.get(f"/api/attempts/{att}", headers=h)
    ids = [x["id"] for x in detail.json()["data"]["highlights"]]
    assert a_id not in ids
    assert len(ids) == 1


async def test_delete_non_owner_404(
    client, auth_headers, make_user, make_exam
):
    """HD4 — deleting someone else's highlight → 404 (don't leak)."""
    owner = await make_user(email="hd4-o@x.com", role="student")
    other = await make_user(email="hd4-x@x.com", role="student")
    exam = await make_exam()
    att = (await _start(client, _student(auth_headers, owner), exam["id"])).json()[
        "data"
    ]["attemptId"]
    hl_id = (await _mk_hl(client, _student(auth_headers, owner), att)).json()[
        "data"
    ]["highlight"]["id"]
    r = await client.delete(
        f"/api/attempts/{att}/highlights/{hl_id}",
        headers=_student(auth_headers, other),
    )
    assert r.status_code == 404


# ===================================================================== #
# Embed (HL)                                                           #
# ===================================================================== #


async def test_resume_payload_includes_highlights(
    client, auth_headers, make_user, make_exam
):
    """HL2 — resume (start Case B) returns highlights[]."""
    s = await make_user(email="hl2@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    await _mk_hl(client, h, att, quotedText="World")
    resume = await _start(client, h, exam["id"])  # same exam → Case B
    assert resume.status_code == 200
    data = resume.json()["data"]
    assert data["isResume"] is True
    assert any(x["quotedText"] == "World" for x in data["highlights"])


async def test_detail_includes_highlights_with_note(
    client, auth_headers, make_user, make_exam
):
    """HL4 — GET detail returns highlights[] with note."""
    s = await make_user(email="hl4@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    await _mk_hl(client, h, att, note="ghi chú")
    detail = await client.get(f"/api/attempts/{att}", headers=h)
    hls = detail.json()["data"]["highlights"]
    assert len(hls) == 1 and hls[0]["note"] == "ghi chú"


async def test_fresh_start_highlights_empty(
    client, auth_headers, make_user, make_exam
):
    """HL3 — fresh start (Case A) → highlights: []."""
    s = await make_user(email="hl3@x.com", role="student")
    exam = await make_exam()
    r = await _start(client, _student(auth_headers, s), exam["id"])
    assert r.status_code == 201
    assert r.json()["data"]["highlights"] == []


# ===================================================================== #
# RBAC visibility (HR)                                                 #
# ===================================================================== #


async def test_teacher_in_class_sees_highlights_on_review(
    client, auth_headers, make_user, make_exam, make_class
):
    """HR1 — teacher who shares a class sees the student's highlights."""
    teacher = await make_user(email="hr1-t@x.com", role="teacher")
    student = await make_user(email="hr1-s@x.com", role="student")
    await make_class(
        name="HR1", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    att = (await _start(client, _student(auth_headers, student), exam["id"])).json()[
        "data"
    ]["attemptId"]
    await _mk_hl(client, _student(auth_headers, student), att, note="hs note")

    detail = await client.get(
        f"/api/attempts/{att}", headers=_teacher(auth_headers, teacher)
    )
    assert detail.status_code == 200
    hls = detail.json()["data"]["highlights"]
    assert len(hls) == 1 and hls[0]["note"] == "hs note"


async def test_teacher_not_in_class_403(
    client, auth_headers, make_user, make_exam
):
    """HR2 — teacher not sharing a class cannot view the attempt (403)."""
    teacher = await make_user(email="hr2-t@x.com", role="teacher")
    student = await make_user(email="hr2-s@x.com", role="student")
    exam = await make_exam()
    att = (await _start(client, _student(auth_headers, student), exam["id"])).json()[
        "data"
    ]["attemptId"]
    await _mk_hl(client, _student(auth_headers, student), att)
    detail = await client.get(
        f"/api/attempts/{att}", headers=_teacher(auth_headers, teacher)
    )
    assert detail.status_code == 403


# ===================================================================== #
# Edge (HX)                                                            #
# ===================================================================== #


async def test_highlights_scoped_per_attempt(
    client, auth_headers, make_user, make_exam
):
    """HX3 — a new attempt does not inherit a previous attempt's highlights."""
    s = await make_user(email="hx3@x.com", role="student")
    exam = await make_exam()
    h = _student(auth_headers, s)
    att1 = (await _start(client, h, exam["id"])).json()["data"]["attemptId"]
    await _mk_hl(client, h, att1)
    # Abandon → frees the active slot → next start is a fresh attempt.
    await client.post(f"/api/attempts/{att1}/abandon", headers=h)
    fresh = await _start(client, h, exam["id"])
    assert fresh.status_code == 201
    assert fresh.json()["data"]["highlights"] == []
