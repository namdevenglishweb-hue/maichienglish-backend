"""Exam content-edit lock — PL/MD/UP/HT.

An exam's CONTENT (sections/questions/materials) is frozen (409) once it has
ANY attempt — regardless of publish state. Metadata stays editable, and an
exam can ALWAYS be unpublished (even with attempts). Guards live in
services/exam_guards.py.

Integration tests; auto-skipped unless the integration DB is enabled.
Most are service-level (the guard is at the service layer) + one HTTP test
verifying the global ConflictError → 409 handler. See docs/exam-publish-lock/.
"""

import pytest

from services.exam_service import exam_service
from services.exceptions import ConflictError
from services.question_service import question_service
from services.section_service import section_service

pytestmark = pytest.mark.integration

_MC_DATA = {
    "stem": "q",
    "options": [{"text": "a"}, {"text": "b"}],
    "correct_index": 0,
}
_SECTION = {
    "type": "multiple_choice",
    "questions": [{"question_type": "multiple_choice", "question_data": _MC_DATA}],
}


async def _exam_with_attempt(make_exam, make_user, make_attempt, *, published=True):
    """An exam (published by default) that already has one submitted attempt."""
    exam = await make_exam(published=published, sections=[_SECTION])
    student = await make_user(email=f"pl-{exam['id'][:8]}@x.com", role="student")
    await make_attempt(student["id"], exam["id"], state="submitted")
    return exam


# ===================================================================== #
# Content locked when the exam HAS ATTEMPTS (PL) — service-level         #
# ===================================================================== #


async def test_update_section_blocked_with_attempts(make_exam, make_user, make_attempt):
    """PL1"""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await section_service.update_section(sid, instructions="x")


async def test_soft_delete_section_blocked_with_attempts(make_exam, make_user, make_attempt):
    """PL2"""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await section_service.soft_delete_section(sid)


async def test_create_question_blocked_with_attempts(make_exam, make_user, make_attempt):
    """PL3"""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await question_service.create_question(sid, "multiple_choice", _MC_DATA)


async def test_update_question_blocked_with_attempts(make_exam, make_user, make_attempt):
    """PL4"""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    qid = exam["sections"][0]["questions"][0]["id"]
    with pytest.raises(ConflictError):
        await question_service.update_question(qid, points=2)


async def test_bulk_update_sections_blocked_with_attempts(make_exam, make_user, make_attempt):
    """PL5"""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await section_service.bulk_update_sections([{"id": sid, "instructions": "x"}])


async def test_bulk_delete_questions_blocked_with_attempts(make_exam, make_user, make_attempt):
    """PL6"""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    qid = exam["sections"][0]["questions"][0]["id"]
    with pytest.raises(ConflictError):
        await question_service.bulk_delete_questions([qid])


async def test_draft_section_edit_allowed(make_exam):
    """PL7 — content with NO attempts is editable (draft)."""
    exam = await make_exam(published=False, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    out = await section_service.update_section(sid, instructions="ok")
    assert out["instructions"] == "ok"


async def test_draft_create_question_allowed(make_exam):
    """PL8"""
    exam = await make_exam(published=False, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    q = await question_service.create_question(sid, "multiple_choice", _MC_DATA)
    assert q["id"]


async def test_published_content_editable_without_attempts(make_exam):
    """PL9 — publish no longer freezes content; with NO attempts it stays editable."""
    exam = await make_exam(published=True, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    out = await section_service.update_section(sid, instructions="still editable")
    assert out["instructions"] == "still editable"


# ===================================================================== #
# Metadata always editable (MD)                                         #
# ===================================================================== #


async def test_metadata_edit_allowed_with_attempts(make_exam, make_user, make_attempt):
    """MD1 — PATCH metadata is allowed even with attempts."""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    out = await exam_service.update_exam(exam["id"], title="New Title")
    assert out["title"] == "New Title"


# ===================================================================== #
# Unpublish always allowed (UP)                                         #
# ===================================================================== #


async def test_unpublish_allowed_when_no_attempts(make_exam):
    """UP1"""
    exam = await make_exam(published=True, sections=[_SECTION])
    out = await exam_service.unpublish_exam(exam["id"])
    assert out["is_published"] is False


async def test_unpublish_allowed_with_attempts(make_exam, make_user, make_attempt):
    """UP2 — unpublish is now allowed even when the exam has attempts."""
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    out = await exam_service.unpublish_exam(exam["id"])
    assert out["is_published"] is False


# ===================================================================== #
# HTTP — global ConflictError → 409 handler (HT)                       #
# ===================================================================== #


async def test_http_content_edit_409_when_attempts(
    client, auth_headers, make_exam, make_user, make_attempt
):
    """HT1 — editing content of an exam WITH attempts surfaces ConflictError
    as HTTP 409 via the global handler (the section route maps only
    NotFound/Validation locally)."""
    admin = auth_headers("admin@x.com", role="admin")
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    sid = exam["sections"][0]["id"]
    r = await client.put(
        f"/api/sections/{sid}", json={"instructions": "x"}, headers=admin
    )
    assert r.status_code == 409


async def test_http_unpublish_allowed_with_attempts(
    client, auth_headers, make_exam, make_user, make_attempt
):
    """HT2 — unpublish endpoint succeeds (200) even with attempts."""
    admin = auth_headers("admin@x.com", role="admin")
    exam = await _exam_with_attempt(make_exam, make_user, make_attempt)
    r = await client.post(f"/api/exams/{exam['id']}/unpublish", headers=admin)
    assert r.status_code == 200
