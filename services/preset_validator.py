"""Validate a section against its Part preset — field-coded, FE-mappable.

Reusable at: (a) the AI-output gate (clear messages + feeds retry_error),
(b) builder-save (create_exam_nested / create_section_with_questions — B5),
(c) kho-đề audit. Checks the load-bearing STRUCTURE (section type, question
count, question type, option count). Materials structure vs preset is NOT
checked here — it varies too much across the 30 Parts (single text / multi
text / audio / picture-options) to assert generically; the AI-gen path enforces
its own 1-text shape via the Tầng-B preset skeleton, and the builder validates
materials per-type in _validate_materials.
"""

from dataclasses import dataclass

from services.exceptions import ValidationError
from services.presets import PartPreset, resolve_preset


@dataclass(frozen=True)
class FieldError:
    code: str
    field: str
    message: str


# Code → default message (B7: GET /api/presets/error-codes serves this so the
# FE has one source for inline messages). `field` is a path hint.
ERROR_CODES: dict[str, dict[str, str]] = {
    "PRESET_SECTION_TYPE": {
        "field": "type",
        "messageEn": "Section type does not match the Cambridge Part.",
        "messageVi": "Loại phần thi không khớp khuôn Cambridge.",
    },
    "PRESET_NUM_QUESTIONS": {
        "field": "questions",
        "messageEn": "Wrong number of questions for this Part.",
        "messageVi": "Sai số câu so với khuôn của Part này.",
    },
    "PRESET_QUESTION_TYPE": {
        "field": "questions[i].question_type",
        "messageEn": "Question type does not match the Part.",
        "messageVi": "Loại câu hỏi không khớp khuôn.",
    },
    "PRESET_OPTIONS": {
        "field": "questions[i].options",
        "messageEn": "Wrong number of options for this question.",
        "messageVi": "Sai số lựa chọn của câu hỏi.",
    },
}


def validate_output_against_preset(
    section: dict, preset: PartPreset
) -> list[FieldError]:
    """Return a list of FieldError (empty = conforms). Structure-only."""
    errs: list[FieldError] = []

    if section.get("type") != preset.section_type:
        errs.append(FieldError(
            "PRESET_SECTION_TYPE", "type",
            f"section.type phải là {preset.section_type!r} theo {preset.part_code}"))

    qs = section.get("questions") or []
    if len(qs) != preset.num_questions:
        errs.append(FieldError(
            "PRESET_NUM_QUESTIONS", "questions",
            f"{preset.part_code} chuẩn có {preset.num_questions} câu — đang có {len(qs)}"))

    for i, q in enumerate(qs):
        if not isinstance(q, dict):
            continue
        if q.get("question_type") != preset.question_type:
            errs.append(FieldError(
                "PRESET_QUESTION_TYPE", f"questions[{i}].question_type",
                f"Câu {i + 1} phải là {preset.question_type!r}"))
        # Option count only applies to choice-style questions (preset declares
        # a number); fill_blank/form_completion/writing/speaking have None.
        if preset.options_per_question is not None:
            opts = (q.get("question_data") or {}).get("options") or []
            if len(opts) != preset.options_per_question:
                errs.append(FieldError(
                    "PRESET_OPTIONS", f"questions[{i}].options",
                    f"Câu {i + 1} cần {preset.options_per_question} lựa chọn — "
                    f"đang có {len(opts)}"))
    return errs


def assert_section_matches_preset(part_code, section_type, questions) -> None:
    """Builder-save gate (B5): hard-block on structural mismatch.

    - No part_code → no-op (custom section, hành vi cũ).
    - Unknown part_code → ValidationError (via resolve_preset).
    - Partial section (no questions yet — granular CRUD) → only the part_code is
      validated as known; structural check is deferred (same policy as gap
      markers: not enforced on granular CRUD).
    - Full section (has questions) → structure must match the preset, else
      ValidationError listing field-coded messages for the FE to map inline.
    """
    if not part_code:
        return
    preset = resolve_preset(part_code)   # ValidationError if unknown
    if not questions:
        return
    errs = validate_output_against_preset(
        {"type": section_type, "questions": questions}, preset)
    if errs:
        raise ValidationError(
            f"Sai khuôn {part_code}: "
            + "; ".join(f"[{e.code}] {e.message}" for e in errs)
        )


def error_code_catalog() -> list[dict]:
    """B7 — serializable list of validator codes + default messages."""
    return [{"code": code, **info} for code, info in ERROR_CODES.items()]
