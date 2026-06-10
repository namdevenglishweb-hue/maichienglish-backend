"""Prompt construction + tool schemas for AI exam generation.

Pure, provider-neutral building blocks consumed by the adapter
(`anthropic_generator.py`). Keeps all wording + JSON schemas in one place.
See `docs/exam-ai-generation/exam-ai-generation-design.md` §3, §6, §7, §10.

Design contract reminders:
  - Structure invariants (§4) are enforced in code, not trusted from the
    model — but we still tell the model so it doesn't fight us.
  - Media `url`/`type`, question `question_type`/`points`, section
    `type`/`max_audio_plays` are re-imposed from the source by the caller.
  - Admin prompt priority: invariants/quality > K > per-type (A) > per-section (B).
"""

import json
from typing import Any, Optional

# ---------------------------------------------------------------------------
# K — variation level (§3). 1 = minimal, 5 = near-new (structure preserved).
# ---------------------------------------------------------------------------

K_INSTRUCTIONS: dict[int, str] = {
    1: ("K=1 (minimal): change ONLY proper nouns, numbers and place names. "
        "Keep the same topic, difficulty, length and sentence structures."),
    2: ("K=2 (light): swap names plus a few minor details and reword a handful "
        "of sentences. Keep the overall topic and difficulty."),
    3: ("K=3 (moderate): change the topic/scenario (e.g. football -> badminton). "
        "Keep the same difficulty, length and question style."),
    4: ("K=4 (heavy): use a new scenario and reword almost everything; you may "
        "restructure sentences. Keep the same difficulty band and question count."),
    5: ("K=5 (near-new): write an essentially new passage of the same exam type "
        "and difficulty. Preserve only the structural mechanics (number of "
        "questions, their types, the answering/marking scheme)."),
}

MIN_K, MAX_K = 1, 5

# ---------------------------------------------------------------------------
# System prompts (cached on the provider side).
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_GENERATE = """\
You rewrite a single section of a real English exam (KET/PET/IELTS style) into \
a NEW version with different content but the SAME structure. This is a real exam, \
so correctness matters above everything: the material and the questions must be \
mutually consistent and every answer key must be correct.

HARD INVARIANTS — never break these (they are also enforced in code):
- Keep the SAME number of materials, in the same order, each the same type.
- For audio/image materials: keep the file `url` byte-for-byte. You may only \
rewrite the text content and the `meta` (audio.meta.transcript / \
image.meta.description) and set meta.pendingReplacement=true.
- Keep the SAME number of questions, same order, same question_type, same points.
- For multiple_choice / matching: keep the SAME number of options. You MAY move \
which option is correct, but `correct_index` must stay a valid index and must \
actually be correct given the new content.
- For fill_blank: keep the SAME number of blanks; every `{{gap:N}}` marker in \
text must stay and resolve to a question. (form_completion sections have no \
`{{gap:N}}` — keep the per-blank label/prefix/postfix structure in question_data.)

WHAT YOU CHANGE (scaled by K): passage/text content, transcripts/descriptions, \
stems, option texts, fill-in answers, part_label/instructions wording.

QUALITY BAR: every question must be answerable from this section's material \
alone; distractors must be plausible-but-wrong with exactly one correct option; \
keep the level's style and difficulty; natural, error-free English.

MEDIA: audio/image are real files you cannot hear/see. Use the source \
material.meta (transcript/description) as your raw input, and emit a NEW \
transcript/description for the imagined new media.

Admin guidance (if present) is a PREFERENCE only and never overrides the \
invariants or answer-correctness. Return your result by calling the \
`emit_section` tool. For each multiple_choice/matching question include a short \
`answer_justification` mapping the correct answer to evidence in the new text.\
"""

SYSTEM_PROMPT_VERIFY = """\
You are an independent exam reviewer (NOT the author). Judge whether a generated \
exam section is correct and usable as a real English exam section. Be strict.

Check, for the section as a whole:
- Material<->question coherence: every question is answerable from this \
section's material/transcript alone; no orphan questions.
- Answer correctness: for multiple_choice/matching, correct_index is truly the \
correct option; for fill_blank, each correct_answers entry is right for its blank.
- Distractors are plausible-but-wrong; exactly one correct option per question.
- Right type & difficulty for the level; natural, error-free English.
- If a listening/image question exists, it matches the new \
material.meta.transcript / meta.description.

Report by calling the `report_review` tool. Mark severity 'critical' for wrong \
answers or unanswerable questions, 'minor' for wording. If anything is \
'critical' or 'minor', also return a corrected `fixed_section` (same shape as \
emit_section) that fixes every issue while preserving all structure.\
"""

# ---------------------------------------------------------------------------
# Tool schemas — force structured output. We re-validate everything in code,
# so item shapes are intentionally permissive (objects), not exhaustive.
# ---------------------------------------------------------------------------

EMIT_SECTION_TOOL: dict[str, Any] = {
    "name": "emit_section",
    "description": "Return the rewritten section (same structure, new content).",
    "input_schema": {
        "type": "object",
        "properties": {
            "part_label": {"type": ["string", "null"]},
            "instructions": {"type": ["string", "null"]},
            "materials": {
                "type": "array",
                "description": "Same length/order/type as source; media url unchanged.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": ["string", "null"], "description": "text | audio | image (unchanged from source)."},
                        "content": {"type": ["string", "null"], "description": "For text: the new passage."},
                        "label": {"type": ["string", "null"]},
                        "url": {"type": ["string", "null"], "description": "For audio/image: leave unchanged."},
                        "alt": {"type": ["string", "null"]},
                        "meta": {"type": ["object", "null"], "description": "For audio/image: transcript/description."},
                    },
                },
            },
            "questions": {
                "type": "array",
                "description": "Same length/order/question_type as source.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_type": {"type": ["string", "null"], "description": "Unchanged from source."},
                        "question_data": {
                            "type": "object",
                            "description": "REQUIRED. New content under the SAME shape as the "
                            "source question's question_data (e.g. stem/options/correct_index, "
                            "or the {{gap:N}} blank structure). Never omit this wrapper.",
                        },
                        "answer_justification": {"type": ["string", "null"]},
                    },
                    "required": ["question_data"],
                },
            },
        },
        "required": ["materials", "questions"],
    },
}

VERIFY_SECTION_TOOL: dict[str, Any] = {
    "name": "report_review",
    "description": "Report whether the section is acceptable + fixes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_acceptable": {"type": "boolean"},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["critical", "minor"]},
                        "question_position": {"type": ["integer", "null"]},
                        "problem": {"type": "string"},
                        "fix": {"type": ["string", "null"]},
                    },
                    "required": ["severity", "problem"],
                },
            },
            "fixed_section": {
                "type": ["object", "null"],
                "description": "Optional corrected section in the SAME shape as the "
                "`emit_section` output (materials[] + questions[] where each question "
                "keeps its `question_data` wrapper). null/omit if no fix needed.",
            },
        },
        "required": ["is_acceptable", "issues"],
    },
}

# ---------------------------------------------------------------------------
# Payload + message rendering
# ---------------------------------------------------------------------------


def build_section_payload(
    source_section: dict[str, Any],
    exam_context: dict[str, Any],
    *,
    type_prompt: Optional[str] = None,
    section_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the provider-neutral payload for one section (§6.1).

    `source_section` carries type/part_label/instructions/max_audio_plays/
    materials (with meta) /questions (WITH answers — not stripped, §1).
    """
    return {
        "exam_context": exam_context,
        "section": source_section,
        "type_prompt": type_prompt,
        "section_prompt": section_prompt,
    }


def _admin_blocks(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    if payload.get("type_prompt"):
        parts.append(
            "### ADMIN GUIDANCE FOR THIS SECTION TYPE (preference, never "
            f"overrides invariants)\n{payload['type_prompt']}"
        )
    if payload.get("section_prompt"):
        parts.append(
            "### ADMIN GUIDANCE FOR THIS SPECIFIC SECTION (preference, takes "
            f"precedence over the type guidance)\n{payload['section_prompt']}"
        )
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def render_generate_user_message(payload: dict[str, Any], *, k: int) -> str:
    """User turn for generate_section: K + admin prompts + source section."""
    ctx = payload.get("exam_context") or {}
    section_json = json.dumps(payload["section"], ensure_ascii=False, indent=2)
    retry = payload.get("retry_error")
    retry_block = (
        f"YOUR PREVIOUS ATTEMPT WAS REJECTED: {retry}\nFix this and try again.\n\n"
        if retry else ""
    )
    return (
        f"{K_INSTRUCTIONS[k]}\n\n"
        f"Exam context: level={ctx.get('level')}, skill={ctx.get('skill')}, "
        f"title={ctx.get('title')!r}.\n\n"
        f"{_admin_blocks(payload)}{retry_block}"
        "Rewrite the SOURCE SECTION below following all invariants. Return the "
        "result via the `emit_section` tool. Each question MUST stay an object "
        "with `question_type` and a `question_data` object using the SAME keys as "
        "the source (e.g. stem/options/correct_index) — never flatten or omit the "
        "`question_data` wrapper. Keep materials in the same order/type.\n\n"
        f"SOURCE SECTION (JSON, includes answer keys + media meta):\n{section_json}"
    )


def render_verify_user_message(
    section: dict[str, Any], payload: dict[str, Any]
) -> str:
    """User turn for verify_section: the generated section to judge."""
    ctx = payload.get("exam_context") or {}
    section_json = json.dumps(section, ensure_ascii=False, indent=2)
    admin = _admin_blocks(payload)
    intent = (
        f"Admin intent to respect (preference):\n\n{admin}" if admin else ""
    )
    return (
        f"Exam context: level={ctx.get('level')}, skill={ctx.get('skill')}.\n\n"
        f"{intent}"
        "Judge the GENERATED SECTION below and report via `report_review`. If "
        "anything is wrong, return a corrected `fixed_section`.\n\n"
        f"GENERATED SECTION (JSON):\n{section_json}"
    )
