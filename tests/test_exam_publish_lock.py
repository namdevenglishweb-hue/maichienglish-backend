"""Exam publish lock — PL/MD/UP/HT.

Once an exam is published its CONTENT (sections/questions/materials) is
frozen (409); metadata stays editable; unpublish is blocked once the exam
has attempts. Guards live in services/exam_guards.py.

Integration tests; auto-skipped unless the integration DB is enabled.
Most are service-level (the guard is at the service layer) + one HTTP test
verifying the global ConflictError → 409 handler. See
docs/exam-publish-lock/.
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


# ===================================================================== #
# Content locked when published (PL) — service-level                   #
# ===================================================================== #


async def test_update_section_blocked_when_published(make_exam):
    """PL1"""
    exam = await make_exam(published=True, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await section_service.update_section(sid, instructions="x")


async def test_soft_delete_section_blocked_when_published(make_exam):
    """PL2"""
    exam = await make_exam(published=True, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await section_service.soft_delete_section(sid)


async def test_create_question_blocked_when_published(make_exam):
    """PL3"""
    exam = await make_exam(published=True, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await question_service.create_question(sid, "multiple_choice", _MC_DATA)


async def test_update_question_blocked_when_published(make_exam):
    """PL4"""
    exam = await make_exam(published=True, sections=[_SECTION])
    qid = exam["sections"][0]["questions"][0]["id"]
    with pytest.raises(ConflictError):
        await question_service.update_question(qid, points=2)


async def test_bulk_update_sections_blocked_when_published(make_exam):
    """PL5"""
    exam = await make_exam(published=True, sections=[_SECTION])
    sid = exam["sections"][0]["id"]
    with pytest.raises(ConflictError):
        await section_service.bulk_update_sections([{"id": sid, "instructions": "x"}])


async def test_bulk_delete_questions_blocked_when_published(make_exam):
    """PL6"""
    exam = await make_exam(published=True, sections=[_SECTION])
    qid = exam["sections"][0]["questions"][0]["id"]
    with pytest.raises(ConflictError):
        await question_service.bulk_delete_questions([qid])


async def test_draft_section_edit_allowed(make_exam):
    """PL7 — draft exam content is editable."""
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


# ===================================================================== #
# Metadata still editable when published (MD)                          #
# ===================================================================== #


async def test_metadata_edit_allowed_when_published(make_exam):
    """MD1 — PATCH metadata of a published exam is allowed."""
    exam = await make_exam(published=True, sections=[_SECTION])
    out = await exam_service.update_exam(exam["id"], title="New Title")
    assert out["title"] == "New Title"


# ===================================================================== #
# Unpublish (UP)                                                       #
# ===================================================================== #


async def test_unpublish_allowed_when_no_attempts(make_exam):
    """UP1"""
    exam = await make_exam(published=True, sections=[_SECTION])
    out = await exam_service.unpublish_exam(exam["id"])
    assert out["is_published"] is False


async def test_unpublish_blocked_when_attempts(make_exam, make_user, make_attempt):
    """UP2 — an exam with any attempt cannot be unpublished."""
    student = await make_user(email="upl-s@x.com", role="student")
    exam = await make_exam(published=True, sections=[_SECTION])
    await make_attempt(student["id"], exam["id"], state="submitted")
    with pytest.raises(ConflictError):
        await exam_service.unpublish_exam(exam["id"])


# ===================================================================== #
# HTTP — global ConflictError → 409 handler (HT)                       #
# ===================================================================== #


async def test_http_unpublish_409_when_attempts(
    client, auth_headers, make_exam, make_user, make_attempt
):
    """HT1 — unpublish endpoint surfaces ConflictError as HTTP 409 via the
    global handler (route itself only maps NotFoundError)."""
    admin = auth_headers("admin@x.com", role="admin")
    student = await make_user(email="uph-s@x.com", role="student")
    exam = await make_exam(published=True, sections=[_SECTION])
    await make_attempt(student["id"], exam["id"], state="submitted")
    r = await client.post(f"/api/exams/{exam['id']}/unpublish", headers=admin)
    assert r.status_code == 409
