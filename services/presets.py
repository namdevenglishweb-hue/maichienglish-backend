"""Cambridge Part presets — code constants ("one source of truth").

This round is MC-only: PET_R_P3, KET_R_P3 (the long-text reading multiple-choice
part). A preset feeds AI generation as the AUTHORITATIVE structure
(num_questions / options_per_question / word-count / CEFR / section_type /
points) — it OVERRIDES what the spec pipeline would otherwise derive from the
source section (decision: "preset quyết cấu trúc, đề gốc bao nhiêu câu cũng kệ").

IMPORTANT — the generate/verify PROMPTS are NOT changed by presets: a preset
only changes the DATA placed into the existing `STRUCTURE SPEC` slot and the
structural skeleton the output is validated against (see
docs/exam-part-presets/ and AMENDMENT v1.2). ANALYZE/leak-check/blind-solve/
similarity guard all stay exactly as the client designed them.

Storage = code constant (git-reviewed, deploys with code), NOT a DB table:
Part format is Cambridge's spec, changes rarely, and changing it must go with
core/prompt/harness changes — it belongs in source control, not runtime admin.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuestionProfile:
    """Per-question skill profile of a Part. CROSS-CHECK / audit only this round
    — NOT injected into the prompt (ANALYZE still authors spec['per_question']).
    Left empty for the presets below by decision."""

    skill_tested: str
    answer_scope: str
    distractor_pattern: str


@dataclass(frozen=True)
class PartPreset:
    part_code: str
    level: str                 # "KET" | "PET"
    skill: str                 # "reading"
    section_type: str          # maps to sections.type
    question_type: str         # maps to questions.question_type
    num_questions: int
    options_per_question: int
    word_count_range: tuple[int, int]
    cefr_level: str
    points_per_question: int = 1
    label: str = ""
    label_vi: str = ""
    # Optional, audit/cross-check only — see QuestionProfile. Empty this round.
    per_question: tuple[QuestionProfile, ...] = field(default_factory=tuple)


PART_PRESETS: dict[str, PartPreset] = {
    "PET_R_P3": PartPreset(
        part_code="PET_R_P3", level="PET", skill="reading",
        section_type="multiple_choice", question_type="multiple_choice",
        num_questions=5, options_per_question=4,
        word_count_range=(220, 320), cefr_level="B1",
        points_per_question=1, label="Part 3",
        label_vi="Bài đọc dài (trắc nghiệm)",
    ),
    "KET_R_P3": PartPreset(
        part_code="KET_R_P3", level="KET", skill="reading",
        section_type="multiple_choice", question_type="multiple_choice",
        num_questions=5, options_per_question=3,
        word_count_range=(150, 230), cefr_level="A2",
        points_per_question=1, label="Part 3",
        label_vi="Bài đọc dài (trắc nghiệm)",
    ),
}


def resolve_preset(part_code):
    """Return the PartPreset for `part_code`, or None when not given.
    Unknown code → ValidationError (→ 400 at the route)."""
    if not part_code:
        return None
    preset = PART_PRESETS.get(part_code)
    if preset is None:
        from services.exceptions import ValidationError
        raise ValidationError(
            f"Unknown part_code {part_code!r}; allowed: "
            f"{', '.join(sorted(PART_PRESETS))}"
        )
    return preset


def structure_facts(preset: PartPreset) -> dict:
    """Authoritative structure facts from a preset — same shape as
    spec_mode.derive_structure_facts, but every field comes from the PRESET
    (not the source). Overlaid onto ANALYZE's qualitative fields by
    spec_mode.merge_structure, then fed into the existing STRUCTURE SPEC slot."""
    return {
        "exam_level": preset.level,
        "cefr_level": preset.cefr_level,
        "skill": preset.skill,
        "section_type": preset.section_type,
        "num_materials": 1,
        "num_questions": preset.num_questions,
        "options_per_question": preset.options_per_question,
        "word_count_range": list(preset.word_count_range),
    }


def preset_skeleton(preset: PartPreset) -> dict:
    """A minimal 'original'-shaped section built from the preset, used as the
    reference for the Tầng-B structural-invariant check (replaces the source as
    the structural mockup). MC reading = 1 text material + N MC questions."""
    return {
        "type": preset.section_type,
        "max_audio_plays": None,
        "materials": [{"type": "text"}],
        "questions": [{
            "question_type": preset.question_type,
            "points": preset.points_per_question,
            "question_data": {"options": [None] * preset.options_per_question},
        } for _ in range(preset.num_questions)],
    }


def list_presets() -> list[dict]:
    """Serializable list for GET /api/presets (FE dropdown). camelCase."""
    return [{
        "partCode": p.part_code, "level": p.level, "skill": p.skill,
        "label": p.label, "labelVi": p.label_vi,
        "sectionType": p.section_type, "questionType": p.question_type,
        "numQuestions": p.num_questions,
        "optionsPerQuestion": p.options_per_question,
        "wordCountRange": list(p.word_count_range),
        "cefrLevel": p.cefr_level, "pointsPerQuestion": p.points_per_question,
    } for p in PART_PRESETS.values()]
