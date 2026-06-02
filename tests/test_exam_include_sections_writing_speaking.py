"""Regression tests — GET /api/exams/{id}?include=sections with writing/speaking.

Bug: the endpoint returned 500 because `ExamSectionPreview.type` was a
`Literal` that didn't include `'writing'`/`'speaking'`. Section creation +
the DB CHECK already allowed those types, so a writing/speaking section
could be created (201) but reading the exam back crashed with a Pydantic
ValidationError → 500.

Coverage in two layers:
  * Unit (always runs, no DB) — the response models accept the new types.
    This is the precise guard for the regression.
  * Integration (needs a live DB) — the full GET route returns 200 for an
    exam with writing + speaking sections, for both admin (un-stripped) and
    student (example-answer fields stripped).
"""

import pytest

from api.exams.schemas import ExamQuestionPreview, ExamSectionPreview


# ---------------------------------------------------------------------------
# Unit — the regression guard. Runs without a DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section_type",
    [
        "writing",
        "speaking",
        "multiple_choice",
        "fill_blank",
        "matching",
        "multiple_choice_shared",
    ],
)
def test_exam_section_preview_accepts_all_section_types(section_type):
    """ExamSectionPreview must serialize every allowed section type —
    including writing/speaking — or ?include=sections 500s."""
    sec = ExamSectionPreview(id="s", position=1, type=section_type)
    assert sec.type == section_type


@pytest.mark.parametrize("qtype", ["writing", "speaking"])
def test_exam_question_preview_accepts_manual_grade_types(qtype):
    q = ExamQuestionPreview(
        id="q", position=1, questionType=qtype,
        questionData={"prompt": "x"}, points=1,
    )
    assert q.questionType == qtype


# ---------------------------------------------------------------------------
# Integration — full route. Needs a live DB (MAICHI_TEST_DB=1).
# ---------------------------------------------------------------------------

_SECTIONS = [
    {
        "type": "writing",
        "questions": [{
            "question_type": "writing",
            "question_data": {"prompt": "Write an email", "exampleAnswer": "Dear..."},
        }],
    },
    {
        "type": "speaking",
        "questions": [{
            "question_type": "speaking",
            "question_data": {"prompt": "Describe a person", "exampleAnswerAudioUrl": "https://x/a.webm"},
        }],
    },
]


@pytest.mark.integration
async def test_get_exam_include_sections_writing_speaking_as_admin(
    client, make_exam, make_user, auth_headers
):
    exam = await make_exam(sections=_SECTIONS)
    await make_user(email="admin@maichienglish.com", role="admin")

    r = await client.get(
        f"/api/exams/{exam['id']}?include=sections",
        headers=auth_headers("admin@maichienglish.com", role="admin"),
    )

    assert r.status_code == 200
    sections = r.json()["data"]["exam"]["sections"]
    assert {"writing", "speaking"} <= {s["type"] for s in sections}
    # admin is privileged → example-answer fields are NOT stripped
    writing_q = next(q for s in sections if s["type"] == "writing" for q in s["questions"])
    assert writing_q["questionType"] == "writing"
    assert writing_q["questionData"].get("exampleAnswer") == "Dear..."


@pytest.mark.integration
async def test_get_exam_include_sections_writing_speaking_as_student_strips_example(
    client, make_exam, make_user, auth_headers
):
    exam = await make_exam(sections=_SECTIONS)
    await make_user(email="stu@maichienglish.com", role="student")

    r = await client.get(
        f"/api/exams/{exam['id']}?include=sections",
        headers=auth_headers("stu@maichienglish.com", role="student"),
    )

    assert r.status_code == 200
    sections = r.json()["data"]["exam"]["sections"]
    assert {"writing", "speaking"} <= {s["type"] for s in sections}
    # student is non-privileged → admin-only example fields stripped
    writing_q = next(q for s in sections if s["type"] == "writing" for q in s["questions"])
    assert "exampleAnswer" not in writing_q["questionData"]
    speaking_q = next(q for s in sections if s["type"] == "speaking" for q in s["questions"])
    assert "exampleAnswerAudioUrl" not in speaking_q["questionData"]
