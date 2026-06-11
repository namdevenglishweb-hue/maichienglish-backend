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
from dataclasses import dataclass
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# K — variation level (§3). 1 = minimal, 5 = near-new (structure preserved).
# ---------------------------------------------------------------------------

K_INSTRUCTIONS: dict[int, str] = {
    1: ("K=1 (minimal): change proper nouns, numbers and place names, and you may "
        "lightly reword. Same topic and difficulty; stays close to the original."),
    2: ("K=2 (light): change names + several details AND reword MOST sentences in "
        "your own words — do NOT leave sentences identical. Same topic and difficulty."),
    3: ("K=3 (moderate): change the TOPIC/scenario itself (e.g. football → cooking) "
        "and write largely new sentences. Keep the same difficulty, length and "
        "question style."),
    4: ("K=4 (heavy): a NEW scenario; rewrite essentially everything with fresh "
        "wording and sentence structures. Keep the same difficulty band and "
        "question count."),
    5: ("K=5 (near-new): write a BRAND-NEW passage on a DIFFERENT subject of the "
        "same exam type and difficulty. Someone who has seen the source must NOT "
        "recognise it — keep NONE of the original wording or storyline, only the "
        "structural mechanics (number/types of questions, the marking scheme)."),
}

MIN_K, MAX_K = 1, 5

# ---------------------------------------------------------------------------
# System prompts (cached on the provider side).
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_GENERATE = """\
You create a NEW version of one section of a real English exam (KET/PET/IELTS \
style). It must keep the same structural MECHANICS as the source but have \
GENUINELY DIFFERENT content — how much you change is set by the K directive in \
the user message. This is a real exam, so correctness matters above all: the \
material and questions must be mutually consistent and every answer key correct.

VARY THE CONTENT — DO NOT CLONE (this is the #1 failure to avoid):
- "Same structure" means only the COUNT and TYPES of materials/questions/options \
and the marking scheme — NOT the wording. Keeping the original sentences, \
phrasing or storyline and merely swapping names is a FAILURE for any K ≥ 2.
- Obey the K directive for how far to move topic / scenario / wording. At K ≥ 3 \
the topic or scenario itself must change; at K = 5 the passage must read as a \
brand-new text a previous test-taker would not recognise.

HARD INVARIANTS — never break these (also enforced in code):
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
    prompt_version: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the provider-neutral payload for one section (§6.1).

    `source_section` carries type/part_label/instructions/max_audio_plays/
    materials (with meta) /questions (WITH answers — not stripped, §1).
    `prompt_version` rides in the payload so adapters resolve the right
    prompt set without a signature change (see PROMPT_VERSIONS).
    """
    return {
        "exam_context": exam_context,
        "section": source_section,
        "type_prompt": type_prompt,
        "section_prompt": section_prompt,
        "prompt_version": prompt_version or DEFAULT_PROMPT_VERSION,
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
        f"VARIATION LEVEL — {K_INSTRUCTIONS[k]}\n\n"
        f"Exam context: level={ctx.get('level')}, skill={ctx.get('skill')}, "
        f"title={ctx.get('title')!r}.\n\n"
        f"{_admin_blocks(payload)}{retry_block}"
        "Produce a NEW section from the SOURCE below: APPLY the variation level "
        "above (genuinely change the content that much — do not just swap names "
        "unless K=1) while keeping every structural invariant. Return the result "
        "via the `emit_section` tool. Each question MUST stay an object "
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


# ---------------------------------------------------------------------------
# v2 — verify pass also sees the SOURCE + the K directive, so the judge can
# detect (and fix) name-only clones. The GENERATE side is identical to v1 on
# purpose: the K scale is awaiting a client decision and must not change here.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_VERIFY_V2 = SYSTEM_PROMPT_VERIFY + """

SIMILARITY CHECK (you also receive the SOURCE section and the K directive \
used for generation):
- Judge whether the generated content differs enough from the source for that \
K. If K >= 2 and the text/transcript/questions are essentially the source with \
only names, numbers or places swapped, report severity 'critical' with problem \
'near-copy of source', and return a `fixed_section` that genuinely rewrites \
the content to the K directive while preserving every structural invariant \
(same counts/types/options, media urls unchanged, answers correct).
- At K = 1 staying close to the source is EXPECTED — only flag when the text \
is a verbatim copy with nothing meaningfully changed.
- The SOURCE is reference material, NOT the answer key: if the SOURCE itself \
contains an error (e.g. its correct_index contradicts its own transcript), do \
NOT fail the generated section for diverging from it. Judge the GENERATED \
section on its own internal consistency — its answers must be correct given \
ITS OWN material/transcript.\
"""


def render_verify_user_message_v2(
    section: dict[str, Any], payload: dict[str, Any], *, k: int
) -> str:
    """v2 user turn for verify_section: source + K directive + generated."""
    ctx = payload.get("exam_context") or {}
    source_json = json.dumps(payload.get("section") or {}, ensure_ascii=False, indent=2)
    section_json = json.dumps(section, ensure_ascii=False, indent=2)
    admin = _admin_blocks(payload)
    intent = (
        f"Admin intent to respect (preference):\n\n{admin}" if admin else ""
    )
    return (
        f"Exam context: level={ctx.get('level')}, skill={ctx.get('skill')}.\n\n"
        f"VARIATION LEVEL USED — {K_INSTRUCTIONS[k]}\n\n"
        f"{intent}"
        "Judge the GENERATED SECTION below (including the SIMILARITY CHECK "
        "against the SOURCE) and report via `report_review`. If anything is "
        "wrong, return a corrected `fixed_section`.\n\n"
        f"SOURCE SECTION (JSON, the original being varied):\n{source_json}\n\n"
        f"GENERATED SECTION (JSON):\n{section_json}"
    )


# ---------------------------------------------------------------------------
# Prompt-version registry. Adding a v3 = one more PromptVersion entry here —
# no if/else anywhere else. The version travels inside the payload
# (`payload["prompt_version"]`, set by build_section_payload) so adapter
# signatures stay unchanged.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptVersion:
    name: str
    description: str
    system_generate: str
    system_verify: str
    # (payload, k) -> user message
    render_generate: Callable[[dict[str, Any], int], str]
    # (generated_section, payload, k) -> user message
    render_verify: Callable[[dict[str, Any], dict[str, Any], int], str]


PROMPT_VERSIONS: dict[str, PromptVersion] = {
    "v1": PromptVersion(
        name="v1",
        description="Baseline (production default) — verify judges the "
                    "generated section alone.",
        system_generate=SYSTEM_PROMPT_GENERATE,
        system_verify=SYSTEM_PROMPT_VERIFY,
        render_generate=lambda payload, k: render_generate_user_message(payload, k=k),
        render_verify=lambda section, payload, k: render_verify_user_message(section, payload),
    ),
    "v2": PromptVersion(
        name="v2",
        description="Anti-clone candidate — verify also receives the source "
                    "section + K directive and flags/fixes near-copies.",
        system_generate=SYSTEM_PROMPT_GENERATE,  # unchanged: K scale awaits client
        system_verify=SYSTEM_PROMPT_VERIFY_V2,
        render_generate=lambda payload, k: render_generate_user_message(payload, k=k),
        render_verify=lambda section, payload, k: render_verify_user_message_v2(section, payload, k=k),
    ),
}

DEFAULT_PROMPT_VERSION = "v1"


def get_prompt_version(name: Optional[str] = None) -> PromptVersion:
    """Resolve a prompt version by name (None/'' → default). ValueError if unknown."""
    key = name or DEFAULT_PROMPT_VERSION
    try:
        return PROMPT_VERSIONS[key]
    except KeyError:
        raise ValueError(
            f"Unknown promptVersion {key!r}; allowed: {', '.join(sorted(PROMPT_VERSIONS))}"
        )
