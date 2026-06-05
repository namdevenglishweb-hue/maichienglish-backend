"""Tests for utils/grading_utils.py — per-type grading + strip_correct."""

import pytest

from utils.grading_utils import grade_question, strip_correct


# ---------------------------------------------------------------------------
# multiple_choice
# ---------------------------------------------------------------------------


def test_mc_correct_index_grades_true():
    qdata = {"stem": "?", "options": ["a", "b", "c"], "correct_index": 1}
    assert grade_question("multiple_choice", qdata, 1) is True


def test_mc_wrong_index_grades_false():
    qdata = {"stem": "?", "options": ["a", "b", "c"], "correct_index": 1}
    assert grade_question("multiple_choice", qdata, 0) is False


def test_mc_non_int_answer_grades_false():
    """If the FE sends "1" (string) instead of 1, must be rejected — guards
    against silent type coercion."""
    qdata = {"stem": "?", "options": ["a", "b", "c"], "correct_index": 1}
    assert grade_question("multiple_choice", qdata, "1") is False


# ---------------------------------------------------------------------------
# matching — shares the multiple_choice grading path
# ---------------------------------------------------------------------------


def test_matching_grades_via_multiple_choice_path():
    qdata = {"stem": "?", "options": ["X", "Y", "Z"], "correct_index": 2}
    assert grade_question("matching", qdata, 2) is True
    assert grade_question("matching", qdata, 0) is False


# ---------------------------------------------------------------------------
# fill_blank
# ---------------------------------------------------------------------------


def test_fill_blank_case_insensitive_default():
    qdata = {"correct_answers": ["Hello"]}
    assert grade_question("fill_blank", qdata, "HELLO") is True
    assert grade_question("fill_blank", qdata, "hello") is True


def test_fill_blank_case_sensitive_enforces_case():
    qdata = {"correct_answers": ["Hello"], "case_sensitive": True}
    assert grade_question("fill_blank", qdata, "Hello") is True
    assert grade_question("fill_blank", qdata, "hello") is False


def test_fill_blank_strips_whitespace():
    qdata = {"correct_answers": ["answer"]}
    assert grade_question("fill_blank", qdata, "  answer  ") is True


def test_fill_blank_multiple_correct_answers():
    qdata = {"correct_answers": ["lift", "elevator"]}
    assert grade_question("fill_blank", qdata, "lift") is True
    assert grade_question("fill_blank", qdata, "elevator") is True
    assert grade_question("fill_blank", qdata, "escalator") is False


def test_fill_blank_non_string_answer_grades_false():
    qdata = {"correct_answers": ["123"]}
    assert grade_question("fill_blank", qdata, 123) is False


# ---------------------------------------------------------------------------
# Common edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("qtype", ["multiple_choice", "fill_blank", "matching"])
def test_none_answer_is_always_false(qtype):
    """Skipped questions must grade as wrong, never crash."""
    qdata = {"correct_index": 0, "correct_answers": ["x"]}
    assert grade_question(qtype, qdata, None) is False


def test_unknown_question_type_grades_false():
    assert grade_question("essay", {"correct": "anything"}, "anything") is False


# ---------------------------------------------------------------------------
# strip_correct — removes answer fields before serving to in-progress students
# ---------------------------------------------------------------------------


def test_strip_correct_removes_correct_index_for_mc():
    qdata = {"stem": "?", "options": ["a", "b"], "correct_index": 1}
    stripped = strip_correct("multiple_choice", qdata)

    assert "correct_index" not in stripped
    assert stripped["stem"] == "?"
    assert stripped["options"] == ["a", "b"]


def test_strip_correct_removes_correct_index_for_matching():
    qdata = {"stem": "?", "options": ["A", "B"], "correct_index": 0}
    stripped = strip_correct("matching", qdata)
    assert "correct_index" not in stripped


def test_strip_correct_removes_correct_answers_and_case_flag_for_fill_blank():
    qdata = {
        "stem": "Fill: {{gap:1}}",
        "correct_answers": ["lift", "elevator"],
        "case_sensitive": True,
    }
    stripped = strip_correct("fill_blank", qdata)

    assert "correct_answers" not in stripped
    assert "case_sensitive" not in stripped
    assert stripped["stem"] == "Fill: {{gap:1}}"


def test_strip_correct_keeps_form_completion_presentation_fields():
    """form_completion blanks reuse fill_blank: answers are stripped, but the
    label/prefix/postfix context stays visible to the in-progress student."""
    qdata = {
        "label": "Time:",
        "prefix": "from",
        "postfix": "to 5 p.m.",
        "correct_answers": ["3 p.m."],
        "case_sensitive": False,
    }
    stripped = strip_correct("fill_blank", qdata)

    assert "correct_answers" not in stripped
    assert "case_sensitive" not in stripped
    assert stripped["label"] == "Time:"
    assert stripped["prefix"] == "from"
    assert stripped["postfix"] == "to 5 p.m."


def test_fill_blank_grading_ignores_presentation_fields():
    """Presence of label/prefix/postfix does not change string-match grading."""
    qdata = {
        "label": "Teacher's name:",
        "prefix": "Mr",
        "correct_answers": ["Brown"],
    }
    assert grade_question("fill_blank", qdata, "Brown") is True
    assert grade_question("fill_blank", qdata, "brown") is True  # case-insensitive
    assert grade_question("fill_blank", qdata, "Green") is False


def test_strip_correct_does_not_mutate_input():
    qdata = {"stem": "?", "options": ["a"], "correct_index": 0}
    _ = strip_correct("multiple_choice", qdata)

    # Original dict untouched — must be a shallow copy
    assert "correct_index" in qdata
    assert qdata["correct_index"] == 0


def test_strip_correct_unknown_type_returns_copy_unchanged():
    qdata = {"foo": "bar"}
    stripped = strip_correct("essay", qdata)

    assert stripped == qdata
    assert stripped is not qdata  # still a copy
