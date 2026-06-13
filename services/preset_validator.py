"""Validate a section against its Part preset — field-coded, FE-mappable.

Reusable at: (a) the AI-output gate (gives clear messages + feeds retry_error),
(b) future builder save, (c) kho-đề audit script. Structural enforcement in the
gen path is ultimately the Tầng-B preset-skeleton check; this layer exists for
human-readable, field-addressed messages (the FE maps each code to Vietnamese).
"""

from dataclasses import dataclass

from services.presets import PartPreset


@dataclass(frozen=True)
class FieldError:
    code: str
    field: str
    message: str


def validate_output_against_preset(
    section: dict, preset: PartPreset
) -> list[FieldError]:
    """Return a list of FieldError (empty = conforms)."""
    errs: list[FieldError] = []

    if section.get("type") != preset.section_type:
        errs.append(FieldError(
            "PRESET_SECTION_TYPE", "type",
            f"section.type phải là {preset.section_type!r} theo {preset.part_code}"))

    mats = section.get("materials") or []
    texts = [m for m in mats if isinstance(m, dict) and m.get("type") == "text"]
    if len(mats) != 1 or len(texts) != 1:
        errs.append(FieldError(
            "PRESET_MATERIALS", "materials",
            f"{preset.part_code} cần đúng 1 material dạng text (đang có {len(mats)})"))

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
        opts = (q.get("question_data") or {}).get("options") or []
        if len(opts) != preset.options_per_question:
            errs.append(FieldError(
                "PRESET_OPTIONS", f"questions[{i}].options",
                f"Câu {i + 1} cần {preset.options_per_question} lựa chọn — "
                f"đang có {len(opts)}"))
    return errs
