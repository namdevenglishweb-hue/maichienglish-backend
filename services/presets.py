"""Cambridge Part presets — code constants ("one source of truth").

A preset = the fixed structure of one Cambridge "Part" (số câu, số option,
materials, word-count, CEFR…). Shared by THREE consumers:
  - builder scaffold (create section/exam đúng khuôn) — B3/B4
  - preset_validator (đối chiếu output) — B5
  - AI generate (preset đè cấu trúc đề gốc) — ✅ shipped, MC-only

Storage = code constant (git-reviewed, deploys with code), NOT a DB table:
Part format is Cambridge's spec, changes rarely, and changing it must go with
core/prompt/harness changes. The DB only persists which preset a section uses
(`sections.part_code`, migration 0024).

Scope of AI generation: only presets whose `ai_core` is in `AI_GEN_CORES`
(currently {"multiple_choice"}) can be AI-generated this round. Other parts
carry their INTENDED core name (e.g. "mc_cloze", "constraint_matrix") for
documentation/future cores, or None for image-dependent parts — builder &
scaffold still work for all of them; only AI-gen is gated.

NOTE: word_count_range + instructions_en here are an initial dataset (sourced
from the client amendment Phụ lục A / Cambridge format pages); values are
tunable without touching code that consumes them.
"""

from dataclasses import dataclass, field
from typing import Optional

# AI-gen cores implemented this round. AI generation refuses a part whose
# ai_core is not in here (builder/scaffold are NOT gated).
AI_GEN_CORES: frozenset[str] = frozenset({"multiple_choice"})


@dataclass(frozen=True)
class MaterialSpec:
    """One material requirement of a Part (count + type + a render hint group)."""
    type: str            # "text" | "audio" | "image"
    count: int = 1
    note: str = ""       # e.g. render_hint group / label convention


@dataclass(frozen=True)
class QuestionProfile:
    """Per-question skill profile — CROSS-CHECK/audit only; NOT fed to the prompt."""
    skill_tested: str
    answer_scope: str
    distractor_pattern: str


@dataclass(frozen=True)
class PartPreset:
    part_code: str
    level: str                              # "KET" | "PET"
    skill: str                              # reading | listening | writing | speaking
    default_position: int                   # vị trí mặc định trong đề full
    label: str
    label_vi: str
    section_type: str                       # maps to sections.type
    question_type: str                      # maps to questions.question_type
    num_questions: int
    options_per_question: Optional[int]     # None: fill_blank/form_completion/writing/speaking
    cefr_level: str
    points_per_question: int = 1
    word_count_range: Optional[tuple[int, int]] = None  # chỉ cho part sinh 1 đoạn text
    gap_markers: bool = False               # material có {{gap:N}}
    shared_options: bool = False            # các câu dùng chung 1 bộ option
    materials_spec: tuple[MaterialSpec, ...] = field(default_factory=tuple)
    instructions_en: str = ""               # rubric mặc định (initial; tunable)
    ai_core: Optional[str] = None           # intended core name; None = chưa định
    per_question: tuple[QuestionProfile, ...] = field(default_factory=tuple)


def _p(**kw) -> PartPreset:
    return PartPreset(**kw)


_T = MaterialSpec  # shorthand for the data block


PART_PRESETS: dict[str, PartPreset] = {
    # ---------------- PET Reading (6) ----------------
    "PET_R_P1": _p(part_code="PET_R_P1", level="PET", skill="reading", default_position=1,
        label="Part 1", label_vi="Biển báo & tin nhắn ngắn", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=5, options_per_question=3, cefr_level="B1",
        materials_spec=(_T("text", 5, "render_hint: notice/sign/message/email/label"),),
        instructions_en="For each question, choose the correct answer.", ai_core="short_texts"),
    "PET_R_P2": _p(part_code="PET_R_P2", level="PET", skill="reading", default_position=2,
        label="Part 2", label_vi="Ghép người ↔ 8 mô tả", section_type="matching",
        question_type="matching", num_questions=5, options_per_question=8, cefr_level="B1",
        shared_options=True, materials_spec=(_T("text", 8, "label A–H"),),
        instructions_en="Match each person to the most suitable option (A–H).",
        ai_core="constraint_matrix"),
    "PET_R_P3": _p(part_code="PET_R_P3", level="PET", skill="reading", default_position=3,
        label="Part 3", label_vi="Bài đọc dài (trắc nghiệm)", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=5, options_per_question=4, cefr_level="B1",
        word_count_range=(300, 400), materials_spec=(_T("text", 1),),
        instructions_en="Read the text and questions below. For each question, mark the correct letter A, B, C or D.",
        ai_core="multiple_choice"),
    "PET_R_P4": _p(part_code="PET_R_P4", level="PET", skill="reading", default_position=4,
        label="Part 4", label_vi="Điền câu vào bài (8 chọn 5)", section_type="matching",
        question_type="matching", num_questions=5, options_per_question=8, cefr_level="B1",
        gap_markers=True, shared_options=True, materials_spec=(_T("text", 1),),
        instructions_en="Five sentences have been removed. Choose from A–H the one which fits each gap.",
        ai_core="gapped_text"),
    "PET_R_P5": _p(part_code="PET_R_P5", level="PET", skill="reading", default_position=5,
        label="Part 5", label_vi="Cloze từ vựng", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=6, options_per_question=4, cefr_level="B1",
        word_count_range=(120, 160), gap_markers=True, materials_spec=(_T("text", 1),),
        instructions_en="Read the text and choose the correct word for each gap.",
        ai_core="mc_cloze"),
    "PET_R_P6": _p(part_code="PET_R_P6", level="PET", skill="reading", default_position=6,
        label="Part 6", label_vi="Open cloze", section_type="fill_blank",
        question_type="fill_blank", num_questions=6, options_per_question=None, cefr_level="B1",
        word_count_range=(90, 130), gap_markers=True, materials_spec=(_T("text", 1),),
        instructions_en="Write ONE word in each gap.", ai_core="open_cloze"),

    # ---------------- KET Reading (5) ----------------
    "KET_R_P1": _p(part_code="KET_R_P1", level="KET", skill="reading", default_position=1,
        label="Part 1", label_vi="Biển báo & tin nhắn ngắn", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=6, options_per_question=3, cefr_level="A2",
        materials_spec=(_T("text", 6, "render_hint: notice/sign/message/email/label"),),
        instructions_en="For each question, choose the correct answer.", ai_core="short_texts"),
    "KET_R_P2": _p(part_code="KET_R_P2", level="KET", skill="reading", default_position=2,
        label="Part 2", label_vi="7 câu hỏi ↔ 3 bài", section_type="multiple_choice_shared",
        question_type="multiple_choice", num_questions=7, options_per_question=3, cefr_level="A2",
        shared_options=True, materials_spec=(_T("text", 3, "label A–C"),),
        instructions_en="For each question, choose the correct text (A, B or C).",
        ai_core="constraint_matrix"),
    "KET_R_P3": _p(part_code="KET_R_P3", level="KET", skill="reading", default_position=3,
        label="Part 3", label_vi="Bài đọc dài (trắc nghiệm)", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=5, options_per_question=3, cefr_level="A2",
        word_count_range=(200, 280), materials_spec=(_T("text", 1),),
        instructions_en="Read the text and questions below. For each question, mark the correct letter A, B or C.",
        ai_core="multiple_choice"),
    "KET_R_P4": _p(part_code="KET_R_P4", level="KET", skill="reading", default_position=4,
        label="Part 4", label_vi="Cloze từ vựng", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=6, options_per_question=3, cefr_level="A2",
        word_count_range=(80, 110), gap_markers=True, materials_spec=(_T("text", 1),),
        instructions_en="Read the text and choose the correct word for each gap.",
        ai_core="mc_cloze"),
    "KET_R_P5": _p(part_code="KET_R_P5", level="KET", skill="reading", default_position=5,
        label="Part 5", label_vi="Open cloze (email)", section_type="fill_blank",
        question_type="fill_blank", num_questions=6, options_per_question=None, cefr_level="A2",
        word_count_range=(60, 100), gap_markers=True, materials_spec=(_T("text", 1, "email"),),
        instructions_en="Complete the email. Write ONE word in each gap.", ai_core="open_cloze"),

    # ---------------- KET Writing (2) ----------------
    "KET_W_P6": _p(part_code="KET_W_P6", level="KET", skill="writing", default_position=6,
        label="Part 6", label_vi="Email/note ≥25 từ", section_type="writing",
        question_type="writing", num_questions=1, options_per_question=None, cefr_level="A2",
        points_per_question=15, materials_spec=(_T("text", 1, "tình huống + 3 ý bắt buộc"),),
        instructions_en="Write an email/note. Write 25 words or more.", ai_core="task_prompt"),
    "KET_W_P7": _p(part_code="KET_W_P7", level="KET", skill="writing", default_position=7,
        label="Part 7", label_vi="Truyện 3 hình ≥35 từ", section_type="writing",
        question_type="writing", num_questions=1, options_per_question=None, cefr_level="A2",
        points_per_question=15, materials_spec=(_T("image", 3, "story strip"),),
        instructions_en="Write the story shown in the three pictures. Write 35 words or more.",
        ai_core=None),  # đợt 2 (image-dependent)

    # ---------------- PET Writing (2) ----------------
    "PET_W_P1": _p(part_code="PET_W_P1", level="PET", skill="writing", default_position=1,
        label="Part 1", label_vi="Email trả lời ~100 từ", section_type="writing",
        question_type="writing", num_questions=1, options_per_question=None, cefr_level="B1",
        points_per_question=20, materials_spec=(_T("text", 1, "email + 4 notes"),),
        instructions_en="Read the email and write your reply (about 100 words).", ai_core="task_prompt"),
    "PET_W_P2": _p(part_code="PET_W_P2", level="PET", skill="writing", default_position=2,
        label="Part 2", label_vi="Article HOẶC story ~100 từ", section_type="writing",
        question_type="writing", num_questions=1, options_per_question=None, cefr_level="B1",
        points_per_question=20, materials_spec=(_T("text", 2, "2 đề — học sinh chọn 1"),),
        instructions_en="Choose ONE task and write about 100 words.", ai_core="task_prompt"),

    # ---------------- PET Listening (4) ----------------
    "PET_L_P1": _p(part_code="PET_L_P1", level="PET", skill="listening", default_position=1,
        label="Part 1", label_vi="Nghe chọn hình", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=7, options_per_question=3, cefr_level="B1",
        materials_spec=(_T("audio", 7), _T("image", 21)),
        instructions_en="For each question, choose the correct picture.", ai_core=None),  # đợt 2
    "PET_L_P2": _p(part_code="PET_L_P2", level="PET", skill="listening", default_position=2,
        label="Part 2", label_vi="Gist hội thoại ngắn", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=6, options_per_question=3, cefr_level="B1",
        materials_spec=(_T("audio", 6),),
        instructions_en="For each question, choose the correct answer.", ai_core="mc_listening"),
    "PET_L_P3": _p(part_code="PET_L_P3", level="PET", skill="listening", default_position=3,
        label="Part 3", label_vi="Điền notes", section_type="form_completion",
        question_type="fill_blank", num_questions=6, options_per_question=None, cefr_level="B1",
        materials_spec=(_T("audio", 1),),
        instructions_en="Complete the notes. Write ONE or TWO words or a number in each gap.",
        ai_core="notes_completion"),
    "PET_L_P4": _p(part_code="PET_L_P4", level="PET", skill="listening", default_position=4,
        label="Part 4", label_vi="Interview", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=6, options_per_question=3, cefr_level="B1",
        materials_spec=(_T("audio", 1),),
        instructions_en="For each question, choose the correct answer.", ai_core="mc_listening"),

    # ---------------- KET Listening (5) ----------------
    "KET_L_P1": _p(part_code="KET_L_P1", level="KET", skill="listening", default_position=1,
        label="Part 1", label_vi="Nghe chọn hình", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=5, options_per_question=3, cefr_level="A2",
        materials_spec=(_T("audio", 5), _T("image", 15)),
        instructions_en="For each question, choose the correct picture.", ai_core=None),  # đợt 2
    "KET_L_P2": _p(part_code="KET_L_P2", level="KET", skill="listening", default_position=2,
        label="Part 2", label_vi="Điền notes", section_type="form_completion",
        question_type="fill_blank", num_questions=5, options_per_question=None, cefr_level="A2",
        materials_spec=(_T("audio", 1),),
        instructions_en="Complete the notes. Write ONE word or a number in each gap.",
        ai_core="notes_completion"),
    "KET_L_P3": _p(part_code="KET_L_P3", level="KET", skill="listening", default_position=3,
        label="Part 3", label_vi="Hội thoại chi tiết", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=5, options_per_question=3, cefr_level="A2",
        materials_spec=(_T("audio", 1),),
        instructions_en="For each question, choose the correct answer.", ai_core="mc_listening"),
    "KET_L_P4": _p(part_code="KET_L_P4", level="KET", skill="listening", default_position=4,
        label="Part 4", label_vi="Gist 5 đoạn ngắn", section_type="multiple_choice",
        question_type="multiple_choice", num_questions=5, options_per_question=3, cefr_level="A2",
        materials_spec=(_T("audio", 5),),
        instructions_en="For each question, choose the correct answer.", ai_core="mc_listening"),
    "KET_L_P5": _p(part_code="KET_L_P5", level="KET", skill="listening", default_position=5,
        label="Part 5", label_vi="Matching A–H", section_type="matching",
        question_type="matching", num_questions=5, options_per_question=8, cefr_level="A2",
        shared_options=True, materials_spec=(_T("audio", 1),),
        instructions_en="Match each speaker/item to the correct option (A–H).",
        ai_core="listening_matching"),

    # ---------------- KET Speaking (2) ----------------
    "KET_S_P1": _p(part_code="KET_S_P1", level="KET", skill="speaking", default_position=1,
        label="Part 1", label_vi="Phỏng vấn cá nhân (3–4′)", section_type="speaking",
        question_type="speaking", num_questions=1, options_per_question=None, cefr_level="A2",
        materials_spec=(_T("text", 1, "bộ câu hỏi"),),
        instructions_en="Personal interview: answer the examiner's questions.", ai_core="task_prompt"),
    "KET_S_P2": _p(part_code="KET_S_P2", level="KET", skill="speaking", default_position=2,
        label="Part 2", label_vi="Thảo luận thích/không thích — bộ tranh (5–6′)", section_type="speaking",
        question_type="speaking", num_questions=1, options_per_question=None, cefr_level="A2",
        # A2 Key Speaking Part 2 = collaborative discussion driven by a PICTURE set
        # (text prompt is only the special-arrangements adaptation) → image-dependent.
        materials_spec=(_T("image", 1, "bộ tranh thảo luận thích/không thích"),),
        instructions_en="Look at the pictures and discuss what you like and dislike, giving reasons.",
        ai_core=None),  # đợt 2 (image-dependent)

    # ---------------- PET Speaking (4) ----------------
    "PET_S_P1": _p(part_code="PET_S_P1", level="PET", skill="speaking", default_position=1,
        label="Part 1", label_vi="Phỏng vấn (2′)", section_type="speaking",
        question_type="speaking", num_questions=1, options_per_question=None, cefr_level="B1",
        materials_spec=(_T("text", 1, "bộ câu hỏi"),),
        instructions_en="Interview: answer the examiner's questions.", ai_core="task_prompt"),
    "PET_S_P2": _p(part_code="PET_S_P2", level="PET", skill="speaking", default_position=2,
        label="Part 2", label_vi="Mô tả ảnh (1′)", section_type="speaking",
        question_type="speaking", num_questions=1, options_per_question=None, cefr_level="B1",
        materials_spec=(_T("image", 1),),
        instructions_en="Describe the photograph.", ai_core=None),  # đợt 2 (image)
    "PET_S_P3": _p(part_code="PET_S_P3", level="PET", skill="speaking", default_position=3,
        label="Part 3", label_vi="Thảo luận tình huống — tranh màu (4′)", section_type="speaking",
        question_type="speaking", num_questions=1, options_per_question=None, cefr_level="B1",
        # B1 Preliminary Speaking Part 3 = collaborative task on a COLOUR PICTURE
        # (illustrated options); spoken situation + visual prompt → image-dependent.
        materials_spec=(_T("text", 1, "tình huống"), _T("image", 1, "tranh màu các lựa chọn")),
        instructions_en="Look at the colour picture and discuss the options together.",
        ai_core=None),  # đợt 2 (image-dependent)
    "PET_S_P4": _p(part_code="PET_S_P4", level="PET", skill="speaking", default_position=4,
        label="Part 4", label_vi="Hội thoại mở rộng (3′)", section_type="speaking",
        question_type="speaking", num_questions=1, options_per_question=None, cefr_level="B1",
        materials_spec=(_T("text", 1, "câu hỏi"),),
        instructions_en="Discuss the general topic with your partner.", ai_core="task_prompt"),
}


def resolve_preset(part_code: Optional[str]) -> Optional[PartPreset]:
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


def supports_ai_gen(preset: PartPreset) -> bool:
    """True if this preset can be AI-generated this round (core implemented)."""
    return preset.ai_core in AI_GEN_CORES


def structure_facts(preset: PartPreset) -> dict:
    """Authoritative structure facts from a preset — same shape as
    spec_mode.derive_structure_facts, sourced entirely from the PRESET. Only
    called for AI-gen-supported (MC) presets, which always have the numeric
    fields; word_count_range is included only when present."""
    facts = {
        "exam_level": preset.level,
        "cefr_level": preset.cefr_level,
        "skill": preset.skill,
        "section_type": preset.section_type,
        "num_materials": 1,
        "num_questions": preset.num_questions,
        "options_per_question": preset.options_per_question,
    }
    if preset.word_count_range:
        facts["word_count_range"] = list(preset.word_count_range)
    return facts


def preset_skeleton(preset: PartPreset) -> dict:
    """Minimal 'original'-shaped section from the preset, used as the reference
    for the Tầng-B structural-invariant check (replaces the source). MC reading
    = 1 text material + N MC questions."""
    return {
        "type": preset.section_type,
        "max_audio_plays": None,
        "materials": [{"type": "text"}],
        "questions": [{
            "question_type": preset.question_type,
            "points": preset.points_per_question,
            "question_data": {"options": [None] * (preset.options_per_question or 0)},
        } for _ in range(preset.num_questions)],
    }


def _preset_dict(p: PartPreset) -> dict:
    return {
        "partCode": p.part_code, "level": p.level, "skill": p.skill,
        "defaultPosition": p.default_position, "label": p.label, "labelVi": p.label_vi,
        "sectionType": p.section_type, "questionType": p.question_type,
        "numQuestions": p.num_questions, "optionsPerQuestion": p.options_per_question,
        "wordCountRange": list(p.word_count_range) if p.word_count_range else None,
        "cefrLevel": p.cefr_level, "pointsPerQuestion": p.points_per_question,
        "gapMarkers": p.gap_markers, "sharedOptions": p.shared_options,
        "materialsSpec": [{"type": m.type, "count": m.count, "note": m.note}
                          for m in p.materials_spec],
        "instructionsEn": p.instructions_en,
        "aiCore": p.ai_core, "aiGenSupported": supports_ai_gen(p),
        # True if any material is an image → needs image gen/upload (đợt 2 cho AI).
        "imageDependent": any(m.type == "image" for m in p.materials_spec),
    }


def list_presets() -> list[dict]:
    """Serializable list for GET /api/presets (FE dropdown + builder scaffold)."""
    return [_preset_dict(p) for p in PART_PRESETS.values()]


# ---------------------------------------------------------------------------
# Scaffold — build an EMPTY but VALID section from a preset (B3/B4).
# Placeholders are chosen so the result passes the real validators
# (_validate_materials / _validate_question_data / validate_gap_markers) and is
# a legal draft. Teacher fills in the real content afterwards. Pure (no DB):
# the returned dict feeds exam_service.create_exam_nested, which persists
# part_code (migration 0024) — no new DB write path.
# ---------------------------------------------------------------------------

_TEXT_PLACEHOLDER = "[Nội dung mẫu — thay bằng nội dung thật]"
_MEDIA_PLACEHOLDER = "pending://replace"   # non-empty (url min_length=1) + pendingReplacement
_LISTENING_MAX_PLAYS = 2                    # Cambridge: nghe 2 lần


def _is_picture_mc(preset: PartPreset) -> bool:
    """Picture multiple-choice (KET/PET Listening Part 1): the OPTIONS are
    pictures (image_url), the section materials are the audio clips."""
    types = {m.type for m in preset.materials_spec}
    return preset.section_type == "multiple_choice" and "image" in types and "audio" in types


def scaffold_section_from_preset(preset: PartPreset) -> dict:
    """Return an empty-but-valid section dict shaped to the preset, ready to be
    passed (with others) to exam_service.create_exam_nested. Carries part_code."""
    n, L = preset.num_questions, (preset.options_per_question or 0)
    st, qt = preset.section_type, preset.question_type
    listening = preset.skill == "listening"
    picture_mc = _is_picture_mc(preset)

    questions: list[dict] = []
    for i in range(n):
        pos = i + 1
        if qt in ("multiple_choice", "matching"):
            if picture_mc:
                opts = [{"image_url": _MEDIA_PLACEHOLDER} for _ in range(L)]
            else:
                opts = [{"text": chr(65 + j)} for j in range(L)]   # A, B, C…
            qd: dict = {"stem": "", "options": opts, "correct_index": 0}
        elif qt == "fill_blank":
            qd = {"correct_answers": ["?"], "case_sensitive": False}
            if st == "form_completion":
                qd["label"] = f"{pos}."
        elif qt in ("writing", "speaking"):
            qd = {"prompt": preset.instructions_en or _TEXT_PLACEHOLDER}
        else:
            qd = {}
        questions.append({
            "question_type": qt, "question_data": qd,
            "points": preset.points_per_question,
        })

    materials: list[dict] = []
    for m in preset.materials_spec:
        if m.type == "text":
            for j in range(m.count):
                content = _TEXT_PLACEHOLDER
                if preset.gap_markers and m.count == 1:
                    content += " " + " ".join("{{gap:%d}}" % k for k in range(1, n + 1))
                mat: dict = {"type": "text", "content": content}
                if st == "multiple_choice" and m.count == n and m.count > 1:
                    mat["label"] = str(j + 1)                 # short_texts: 1 text ↔ 1 question
                elif st in ("matching", "multiple_choice_shared") and m.count > 1:
                    mat["label"] = chr(65 + j)                # lettered text pool (A, B, C…)
                materials.append(mat)
        elif m.type == "image":
            if picture_mc:
                continue                                       # images are the options, not materials
            for _ in range(m.count):
                materials.append({"type": "image", "url": _MEDIA_PLACEHOLDER,
                                  "meta": {"pendingReplacement": True}})
        elif m.type == "audio":
            for _ in range(m.count):
                materials.append({"type": "audio", "url": _MEDIA_PLACEHOLDER,
                                  "meta": {"pendingReplacement": True}})

    return {
        "part_code": preset.part_code,
        "type": st,
        "part_label": preset.label,
        "instructions": preset.instructions_en,
        "max_audio_plays": _LISTENING_MAX_PLAYS if listening else None,
        "materials": materials,
        "questions": questions,
    }


SCAFFOLD_SKILLS = ("reading", "listening")  # exam.skill CHECK; W/S aren't exams


def presets_for_exam(level: str, skill: str) -> list[PartPreset]:
    """Presets for one exam paper (level+skill), ordered by default_position."""
    return sorted(
        (p for p in PART_PRESETS.values() if p.level == level and p.skill == skill),
        key=lambda p: p.default_position,
    )


def build_scaffold_sections(level: str, skill: str) -> list[dict]:
    """Empty-but-valid section dicts for every Part of (level, skill), in order.
    Raises ValidationError if the combo has no presets / unsupported skill."""
    from services.exceptions import ValidationError
    if skill not in SCAFFOLD_SKILLS:
        raise ValidationError(
            f"skill {skill!r} không scaffold được thành đề (chỉ {SCAFFOLD_SKILLS}); "
            "Writing/Speaking là Part lẻ, không phải 1 bài thi độc lập."
        )
    presets = presets_for_exam(level, skill)
    if not presets:
        raise ValidationError(f"Không có preset cho level={level!r} skill={skill!r}")
    return [scaffold_section_from_preset(p) for p in presets]
