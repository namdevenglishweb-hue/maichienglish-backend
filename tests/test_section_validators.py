"""Tests for services/section_service pure validators.

Two module-level helpers, both DB-free:

- `validate_gap_markers(materials, question_positions, section_label)`
  Used only by nested-create endpoints to catch broken `{{gap:N}}`
  passages at import time.

- `_validate_materials(raw)`
  Discriminated union over text / image / audio block shapes, returns
  the normalized list ready to JSON-encode into sections.materials.

Source of truth: plan §3.5.
"""

import pytest

from services.exceptions import ValidationError
from services.section_service import _validate_materials, validate_gap_markers


# ===========================================================================
# validate_gap_markers
# ===========================================================================


def test_gap_marker_pointing_at_existing_position_is_ok():
    materials = [{"type": "text", "content": "Name: {{gap:1}} Age: {{gap:2}}"}]
    validate_gap_markers(materials, question_positions={1, 2})  # no raise


def test_gap_marker_pointing_at_missing_position_raises():
    materials = [{"type": "text", "content": "Trip date: {{gap:7}}"}]
    with pytest.raises(ValidationError) as exc:
        validate_gap_markers(materials, question_positions={1, 2, 3})

    err = str(exc.value)
    assert "{{gap:7}}" in err
    assert "1, 2, 3" in err  # known positions sorted


def test_gap_marker_in_non_text_material_is_ignored():
    """`{{gap:99}}` inside image alt or audio label doesn't count — only
    text materials are scanned."""
    materials = [
        {"type": "image", "url": "https://x/a.png", "alt": "diagram {{gap:99}}"},
        {"type": "audio", "url": "https://x/a.mp3", "label": "track {{gap:99}}"},
    ]
    validate_gap_markers(materials, question_positions={1})  # no raise


def test_non_dict_material_is_skipped():
    """Defensive: caller may pass mixed garbage during partial parsing —
    the validator must not crash on non-dict items."""
    materials = [None, "string", 42, {"type": "text", "content": "ok {{gap:1}}"}]
    validate_gap_markers(materials, question_positions={1})  # no raise


def test_empty_materials_list_is_ok():
    validate_gap_markers([], question_positions={1, 2})


def test_text_material_without_content_field_is_ok():
    """`content` may be missing on a partial draft — treat as no markers."""
    materials = [{"type": "text"}]
    validate_gap_markers(materials, question_positions=set())


def test_multiple_markers_first_invalid_one_raises():
    materials = [{"type": "text", "content": "{{gap:1}} {{gap:99}} {{gap:2}}"}]
    with pytest.raises(ValidationError) as exc:
        validate_gap_markers(materials, question_positions={1, 2})

    assert "{{gap:99}}" in str(exc.value)


def test_section_label_appears_in_error_message():
    """Nested-create surfaces multiple sections — the label tells
    admin which section failed."""
    materials = [{"type": "text", "content": "{{gap:5}}"}]
    with pytest.raises(ValidationError) as exc:
        validate_gap_markers(
            materials,
            question_positions={1},
            section_label="Part 3",
        )

    assert "Part 3" in str(exc.value)


def test_default_section_label_used_when_omitted():
    materials = [{"type": "text", "content": "{{gap:5}}"}]
    with pytest.raises(ValidationError) as exc:
        validate_gap_markers(materials, question_positions={1})

    assert "section:" in str(exc.value)


# ===========================================================================
# _validate_materials — discriminated union over text/image/audio
# ===========================================================================


def test_materials_none_returns_empty_list():
    assert _validate_materials(None) == []


def test_materials_not_a_list_raises():
    with pytest.raises(ValidationError, match="must be a list"):
        _validate_materials({"type": "text", "content": "x"})


def test_materials_item_not_dict_raises_with_index():
    with pytest.raises(ValidationError) as exc:
        _validate_materials([{"type": "text", "content": "ok"}, "bad", 42])

    assert "materials[1]" in str(exc.value)


def test_materials_unknown_type_raises_listing_allowed():
    with pytest.raises(ValidationError) as exc:
        _validate_materials([{"type": "video", "url": "x"}])

    err = str(exc.value)
    assert "video" in err
    assert "audio" in err and "image" in err and "text" in err


# --- text block ---


def test_text_material_valid():
    out = _validate_materials(
        [{"type": "text", "label": "Passage", "content": "Name: {{gap:1}}"}]
    )
    assert out[0]["type"] == "text"
    assert out[0]["label"] == "Passage"
    assert "{{gap:1}}" in out[0]["content"]


def test_text_material_empty_content_rejected():
    """`content` has Field(min_length=1) — empty string fails Pydantic."""
    with pytest.raises(ValidationError):
        _validate_materials([{"type": "text", "content": ""}])


def test_text_material_missing_content_rejected():
    with pytest.raises(ValidationError):
        _validate_materials([{"type": "text"}])


# --- image block ---


def test_image_material_valid_with_alt():
    out = _validate_materials(
        [
            {
                "type": "image",
                "label": "Form",
                "url": "https://x.supabase.co/form.png",
                "alt": "Booking form with blank fields",
            }
        ]
    )
    assert out[0]["url"].endswith("form.png")
    assert out[0]["alt"] == "Booking form with blank fields"


def test_image_material_valid_without_alt_omits_none():
    """`exclude_none=True` — `alt: None` must NOT appear in the
    serialized JSONB."""
    out = _validate_materials(
        [{"type": "image", "url": "https://x.supabase.co/diagram.png"}]
    )
    assert "alt" not in out[0]
    assert "label" not in out[0]


def test_image_material_missing_url_rejected():
    with pytest.raises(ValidationError):
        _validate_materials([{"type": "image", "alt": "no url here"}])


# --- audio block ---


def test_audio_material_valid():
    out = _validate_materials(
        [
            {
                "type": "audio",
                "label": "Track 1",
                "url": "https://x.supabase.co/audio/track1.mp3",
            }
        ]
    )
    assert out[0]["url"].endswith("track1.mp3")
    assert out[0]["label"] == "Track 1"


def test_audio_material_missing_url_rejected():
    with pytest.raises(ValidationError):
        _validate_materials([{"type": "audio", "label": "no url"}])


# --- mixed ordering ---


def test_materials_preserves_order_and_types():
    out = _validate_materials(
        [
            {"type": "audio", "url": "https://x/a.mp3"},
            {"type": "text", "content": "Listen and fill: {{gap:1}}"},
            {"type": "image", "url": "https://x/i.png"},
        ]
    )
    assert [m["type"] for m in out] == ["audio", "text", "image"]


def test_materials_returns_plain_dicts_safe_for_jsonb():
    out = _validate_materials([{"type": "text", "content": "x"}])

    assert isinstance(out, list)
    assert isinstance(out[0], dict)
    assert not hasattr(out[0], "model_dump")
