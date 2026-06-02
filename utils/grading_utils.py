"""Per-type question grading + correct-answer stripping.

Used by attempt_service.submit (grading) and attempt_service.start
(stripping correct answers from question_data before returning to a
student who is taking the exam).

`matching` shares the `multiple_choice` shape and grading path — each
matching question is one independently-scored row of a shared-options
table (KET Listening P5, Reading P2 etc.). The rendering distinction
lives on `section.type`, not in the grader.
"""
from typing import Any


def strip_correct(question_type: str, question_data: dict) -> dict:
    """Return a shallow copy of question_data with answer fields removed.

    Used when serving a question to a student who hasn't submitted yet.
    Writing/speaking carry an `exampleAnswer` / `exampleAnswerAudioUrl`
    that's admin-only — also stripped here (see WRITING_SPEAKING.md §4.3
    / §5.3).
    """
    stripped = dict(question_data)
    if question_type in ("multiple_choice", "matching"):
        stripped.pop("correct_index", None)
    elif question_type == "fill_blank":
        stripped.pop("correct_answers", None)
        stripped.pop("case_sensitive", None)
    elif question_type == "writing":
        stripped.pop("exampleAnswer", None)
    elif question_type == "speaking":
        stripped.pop("exampleAnswerAudioUrl", None)
    return stripped


# Question types whose answers are scored by a human teacher, not by
# grade_question(). submit_attempt skips auto-grading for these and
# leaves is_correct=NULL / points_earned=0 until the teacher's grade
# endpoint sets them. See WRITING_SPEAKING.md §7.
MANUAL_GRADE_TYPES: frozenset[str] = frozenset({"writing", "speaking"})


def _grade_multiple_choice(student_answer: Any, qdata: dict) -> bool:
    if not isinstance(student_answer, int):
        return False
    return student_answer == qdata.get("correct_index")


def _grade_fill_blank(student_answer: Any, qdata: dict) -> bool:
    if not isinstance(student_answer, str):
        return False
    correct = qdata.get("correct_answers") or []
    case_sensitive = qdata.get("case_sensitive", False)
    if case_sensitive:
        return student_answer.strip() in [c.strip() for c in correct]
    return student_answer.strip().lower() in [c.strip().lower() for c in correct]


def grade_question(
    question_type: str, question_data: dict, student_answer: Any
) -> bool:
    """Return True if student_answer is correct.

    `student_answer` may be None (skipped) — always False.
    Unknown question_type — always False.
    """
    if student_answer is None:
        return False
    if question_type in ("multiple_choice", "matching"):
        return _grade_multiple_choice(student_answer, question_data)
    if question_type == "fill_blank":
        return _grade_fill_blank(student_answer, question_data)
    return False
