"""Tests for services/question_service._validate_question_data.

Pure function — no DB. Validates the per-type `question_data` JSONB
payload at write time (single create + bulk + update). Source of truth:
plan §3.6.

`matching` deliberately shares the multiple_choice validator — each
matching question is one independently-scored row of a shared-options
table (KET Listening P5, Reading P2). Rendering distinction lives on
`section.type`, not in this layer.
"""

import pytest

from services.exceptions import ValidationError
from services.question_service import _validate_question_data


# ---------------------------------------------------------------------------
# multiple_choice
# ---------------------------------------------------------------------------


def test_mc_valid_with_text_options():
    out = _validate_question_data(
        "multiple_choice",
        {
            "stem": "Capital of France?",
            "options": [{"text": "Paris"}, {"text": "London"}, {"text": "Berlin"}],
            "correct_index": 0,
        },
    )
    assert out["stem"] == "Capital of France?"
    assert len(out["options"]) == 3
    assert out["correct_index"] == 0


def test_mc_valid_with_image_url_options_picture_mc():
    """Listening Part 1 — picture MC. All options carry image_url."""
    out = _validate_question_data(
        "multiple_choice",
        {
            "options": [
                {"image_url": "https://x.supabase.co/a.png"},
                {"image_url": "https://x.supabase.co/b.png"},
                {"image_url": "https://x.supabase.co/c.png"},
            ],
            "correct_index": 1,
        },
    )
    assert len(out["options"]) == 3


def test_mc_valid_with_mixed_options():
    """Option may carry text OR image_url OR both."""
    out = _validate_question_data(
        "multiple_choice",
        {
            "options": [
                {"text": "Bus", "image_url": "https://x/bus.png"},
                {"text": "Train"},
                {"image_url": "https://x/car.png"},
            ],
            "correct_index": 2,
        },
    )
    assert out["options"][0]["text"] == "Bus"
    assert out["options"][2]["image_url"].endswith("car.png")


def test_mc_excludes_none_fields_in_output():
    """Pydantic `exclude_none=True` — `stem` omitted when not provided,
    `text` omitted on image-only options. Keeps JSONB clean."""
    out = _validate_question_data(
        "multiple_choice",
        {
            "options": [
                {"image_url": "https://x/a.png"},
                {"image_url": "https://x/b.png"},
            ],
            "correct_index": 0,
        },
    )
    assert "stem" not in out
    assert "text" not in out["options"][0]


def test_mc_rejects_option_with_neither_text_nor_image_url():
    with pytest.raises(ValidationError) as exc:
        _validate_question_data(
            "multiple_choice",
            {
                "options": [{"text": "A"}, {}],
                "correct_index": 0,
            },
        )
    assert "text" in str(exc.value) and "image_url" in str(exc.value)


def test_mc_rejects_single_option():
    """MC requires ≥2 options (Pydantic min_length=2)."""
    with pytest.raises(ValidationError):
        _validate_question_data(
            "multiple_choice",
            {"options": [{"text": "only"}], "correct_index": 0},
        )


def test_mc_rejects_no_options_at_all():
    with pytest.raises(ValidationError):
        _validate_question_data(
            "multiple_choice",
            {"options": [], "correct_index": 0},
        )


def test_mc_rejects_correct_index_out_of_range():
    with pytest.raises(ValidationError) as exc:
        _validate_question_data(
            "multiple_choice",
            {
                "options": [{"text": "A"}, {"text": "B"}],
                "correct_index": 5,
            },
        )
    assert "out of range" in str(exc.value)


def test_mc_rejects_negative_correct_index():
    with pytest.raises(ValidationError):
        _validate_question_data(
            "multiple_choice",
            {
                "options": [{"text": "A"}, {"text": "B"}],
                "correct_index": -1,
            },
        )


def test_mc_rejects_missing_correct_index():
    with pytest.raises(ValidationError):
        _validate_question_data(
            "multiple_choice",
            {"options": [{"text": "A"}, {"text": "B"}]},
        )


# ---------------------------------------------------------------------------
# matching — shares the multiple_choice validator
# ---------------------------------------------------------------------------


def test_matching_uses_mc_validator():
    """Matching reuses MC shape; same validator object — see
    `_VALIDATORS` mapping in question_service."""
    out = _validate_question_data(
        "matching",
        {
            "options": [{"text": "Sandy Bay"}, {"text": "High Wood"}, {"text": "Black Lake"}],
            "correct_index": 1,
        },
    )
    assert out["correct_index"] == 1


def test_matching_rejects_same_violations_as_mc():
    """Out-of-range correct_index must fail for matching too."""
    with pytest.raises(ValidationError):
        _validate_question_data(
            "matching",
            {"options": [{"text": "A"}, {"text": "B"}], "correct_index": 9},
        )


# ---------------------------------------------------------------------------
# fill_blank
# ---------------------------------------------------------------------------


def test_fill_blank_valid_defaults_case_insensitive():
    out = _validate_question_data(
        "fill_blank",
        {"correct_answers": ["lift", "elevator"]},
    )
    assert out["correct_answers"] == ["lift", "elevator"]
    assert out["case_sensitive"] is False


def test_fill_blank_valid_case_sensitive_flag_preserved():
    out = _validate_question_data(
        "fill_blank",
        {"correct_answers": ["Paris"], "case_sensitive": True},
    )
    assert out["case_sensitive"] is True


def test_fill_blank_rejects_empty_correct_answers():
    with pytest.raises(ValidationError):
        _validate_question_data(
            "fill_blank",
            {"correct_answers": []},
        )


def test_fill_blank_rejects_missing_correct_answers():
    with pytest.raises(ValidationError):
        _validate_question_data("fill_blank", {"case_sensitive": True})


# ---------------------------------------------------------------------------
# Unknown / boundary cases
# ---------------------------------------------------------------------------


def test_unknown_question_type_rejected():
    with pytest.raises(ValidationError) as exc:
        _validate_question_data("essay", {"prompt": "Discuss."})
    assert "Unknown question_type" in str(exc.value)


def test_validator_returns_dict_safe_for_jsonb_serialization():
    """Output must be a plain dict (not Pydantic model) so asyncpg can
    JSON-serialize it for the question_data JSONB column."""
    out = _validate_question_data(
        "fill_blank",
        {"correct_answers": ["x"]},
    )
    assert isinstance(out, dict)
    assert not hasattr(out, "model_dump")  # not a BaseModel
