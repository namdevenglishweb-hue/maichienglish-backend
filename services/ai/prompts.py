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
# v3 SPEC MODE — the source section NEVER enters generate/verify prompts.
# Prompts ported near-verbatim from the client's harness-validated amendment
# (§9.1/9.2/9.4), with ONE deliberate deviation: the output contract block is
# rewritten for OUR shape (question_data wrapper + option dicts) — this
# deviation must be re-validated via ab_matrix (design §8/DoD#3).
# ---------------------------------------------------------------------------

ANALYZE_TEMPERATURE = 0.2  # client-validated; analyze is where leak bugs lived
# AMENDMENT v1.2 §9 — spec-mode VERIFY (blind solve) + FIX run cool so the
# examiner solves deterministically (a hot judge invents agreement). Applied
# ONLY on spec_mode versions (the adapter gates on pv.spec_mode); the v2
# rewrite verify keeps its default (~1.0) sampling so its A/B stays valid.
# GENERATE is never given a temperature (stays creative at the provider default).
VERIFY_TEMPERATURE = 0.3

SYSTEM_PROMPT_ANALYZE = """\
You are an exam-design analyst for Cambridge English qualifications (KET/PET).

Analyze the source exam section below and produce a SKILL MAP: an abstract
specification that captures HOW the section tests the candidate, without
copying any of its content.

Rules:
- Do NOT include any sentence, phrase, proper noun, or storyline detail from
the source in your output. The skill map must be fully abstract.
- Do NOT name the topic, domain, subject matter, or product category of the
source anywhere in your output — not in text_genre, not in
distractor_pattern, not in style_notes. Refer to it only generically
(e.g. "the product category being discussed", "the activity described").
This includes synonyms, paraphrases, and category words for the source's
subject matter. Describe HOW the questions test the candidate, never WHAT
the text is about. A reader of your skill map must NOT be able to guess
what the source text was about.
- The `text_genre` field is the single most common place domain detail leaks,
so it has a STRICT contract. It may describe ONLY: (a) the text form
(narrative / email / article / review / advertisement / notice / blog post /
report ...); (b) the narrative voice and register (first-person /
third-person; formal / informal / neutral); and (c) the tone (reflective /
persuasive / factual / light-hearted ...). It MUST NOT mention any events, any
locations or venues, any activities, any relationships between the people in
the text, or the reason the text was written. Describe the FORM of the text,
never its story.
BAD (leaks the situation — forbidden): "first-person narrative by a young
person describing a family experience at a specialized instructional venue"
GOOD (form/voice/tone only): "first-person narrative account written by a
young person, informal register, reflective tone"
- For each question, identify: the reading sub-skill it tests, where in the
material the answer is located (global / paragraph N / single detail), and
the pattern its wrong options follow (how distractors are constructed).
- Estimate the CEFR level of the vocabulary and the word count of the material.
- Describe the text genre and style abstractly.

Return your result by calling the `emit_skill_map` tool.\
"""

EMIT_SKILL_MAP_TOOL: dict[str, Any] = {
    "name": "emit_skill_map",
    "description": "Return the abstract skill map of the source section.",
    "input_schema": {
        "type": "object",
        "properties": {
            "structure": {
                "type": "object",
                "properties": {
                    "exam_level": {"type": "string"},
                    "cefr_level": {"type": "string"},
                    "skill": {"type": ["string", "null"]},
                    "section_type": {"type": ["string", "null"]},
                    "num_questions": {"type": ["integer", "null"]},
                    "options_per_question": {"type": ["integer", "null"]},
                    "text_genre": {"type": "string"},
                    "word_count_range": {
                        "type": "array", "items": {"type": "integer"},
                        "minItems": 2, "maxItems": 2,
                    },
                },
                "required": ["exam_level", "cefr_level", "text_genre",
                             "word_count_range"],
            },
            "per_question": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "skill_tested": {"type": "string"},
                        "answer_scope": {"type": "string"},
                        "distractor_pattern": {"type": "string"},
                    },
                    "required": ["position", "skill_tested", "answer_scope",
                                 "distractor_pattern"],
                },
            },
            "style_notes": {"type": ["string", "null"]},
        },
        "required": ["structure", "per_question"],
    },
}


def render_analyze_user_message(payload: dict[str, Any]) -> str:
    """User turn for analyze_section. The ONLY spec-mode call that sees the
    source. `leak_feedback` carries terms a previous attempt leaked."""
    ctx = payload.get("exam_context") or {}
    section_json = json.dumps(payload["section"], ensure_ascii=False, indent=2)
    feedback = ""
    if payload.get("leak_feedback"):
        feedback = (
            "YOUR PREVIOUS SKILL MAP LEAKED these source-specific terms: "
            f"{', '.join(payload['leak_feedback'])}. Produce a new skill map "
            "that does not contain them or any near-equivalent.\n\n"
        )
    return (
        f"Exam context: level={ctx.get('level')}, skill={ctx.get('skill')}.\n\n"
        f"{feedback}"
        f"SOURCE SECTION (JSON):\n{section_json}"
    )


SYSTEM_PROMPT_GENERATE_SPEC = """\
You are a professional item writer for Cambridge English qualifications.
Write ONE complete, brand-new exam section following the specification in the
user message. You are NOT shown any existing exam — invent all content.

HARD CONSTRAINTS (violating any of these makes the output unusable):
1. The material MUST be about the given topic and text genre.
2. Your material MUST incorporate the given story elements naturally.
3. Follow the structure spec exactly: number of questions, options per
question, word count range, CEFR vocabulary level. The word count range is a
HARD LIMIT — count your words before finalising.
4. Every question must be answerable using ONLY the material you write.
5. Exactly ONE option per question is correct. Wrong options must be
plausible but clearly contradicted by, or absent from, the material
(Cambridge-style distractors). Vary how distractors are constructed across
the questions.
6. Vocabulary must not exceed the stated CEFR level except for proper nouns.

OUTPUT SHAPE (return via the `emit_section` tool):
- exactly ONE material: {type: "text", content: <the passage>}
- exactly N questions (N from the spec), each an object with
question_type "multiple_choice" and a `question_data` object
{stem, options: [{text: ...}] with exactly L entries (L from the spec),
correct_index}. Also give a short answer_justification per question quoting
the evidence in your material.
- also emit a fitting `part_label` and student-facing `instructions`
(do NOT leave them empty).\
"""


def render_generate_spec_user_message(payload: dict[str, Any], *, k: int) -> str:
    """Spec-mode generate user turn — contains NO source text. Spec depth by
    K: 3 = structure+per_question+style, 4 = structure+style, 5 = structure.
    """
    spec = payload.get("spec") or {}
    structure = spec.get("structure") or {}
    seed_json = json.dumps(payload.get("diversity_seed") or {}, ensure_ascii=False, indent=2)
    blocks = [
        f"HARD CONSTRAINT — TOPIC: {payload.get('topic')}",
        f"TEXT GENRE: {payload.get('genre')}",
        "STORY ELEMENTS to incorporate naturally:\n" + seed_json,
        "STRUCTURE SPEC:\n" + json.dumps(structure, ensure_ascii=False, indent=2),
    ]
    if k <= 3 and spec.get("per_question"):
        blocks.append(
            "PER-QUESTION SPEC (each question must test exactly the sub-skill "
            "listed for its position, with its answer located as described in "
            "answer_scope, and wrong options built following "
            "distractor_pattern):\n"
            + json.dumps(spec["per_question"], ensure_ascii=False, indent=2)
        )
    if k <= 4 and spec.get("style_notes"):
        blocks.append(f"STYLE NOTES:\n{spec['style_notes']}")
    retry = payload.get("retry_error")
    if retry:
        blocks.insert(0, f"YOUR PREVIOUS ATTEMPT WAS REJECTED: {retry}\n"
                         "Fix this and try again.")
    return "\n\n".join(blocks)


EMIT_SECTION_SPEC_TOOL: dict[str, Any] = {
    "name": "emit_section",
    "description": "Return the brand-new section written from the spec "
                   "(1 text material + N multiple-choice questions).",
    "input_schema": {
        "type": "object",
        "properties": {
            "part_label": {"type": "string"},
            "instructions": {"type": "string"},
            "materials": {
                "type": "array", "minItems": 1, "maxItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["text"]},
                        "content": {"type": "string"},
                    },
                    "required": ["type", "content"],
                },
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_type": {"type": "string",
                                          "enum": ["multiple_choice"]},
                        "question_data": {
                            "type": "object",
                            "properties": {
                                "stem": {"type": "string"},
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {"text": {"type": "string"}},
                                        "required": ["text"],
                                    },
                                },
                                "correct_index": {"type": "integer"},
                            },
                            "required": ["stem", "options", "correct_index"],
                        },
                        "answer_justification": {"type": ["string", "null"]},
                    },
                    "required": ["question_data"],
                },
            },
        },
        "required": ["part_label", "instructions", "materials", "questions"],
    },
}

# AMENDMENT v1.2 §9.4 — spec VERIFY is now a BLIND SOLVE. The examiner never
# sees the answer key (stripped in render) and never sees the source; it solves
# every question from scratch and reports its own answer + a verbatim evidence
# quote per question. CODE (not the model) compares those answers to the real
# key and decides acceptance — the model has no `is_acceptable` field and no
# `fixed_section` (fixing is a separate, key-aware call, §9.5).
SYSTEM_PROMPT_VERIFY_SPEC = """\
You are an independent Cambridge English examiner sitting this exam section as a
candidate. You are NOT shown the answer key — solve every question yourself,
from scratch, using ONLY the material provided. Do not try to guess which option
the author intended; report the option the material actually supports.

For EVERY question, in order, return one entry in `per_question` with:
- position: the question's position.
- examiner_answer_index: the 0-based option index YOU conclude is correct,
reached independently by reading the material.
- evidence_quote: the EXACT words from the material that justify your answer
(copy them verbatim — do not paraphrase, do not leave this empty). If you
cannot find any wording in the material that supports any option, still give
your best examiner_answer_index, leave the quote as your closest attempt, and
ALSO add a 'critical' issue saying the question is unanswerable from the
material — never silently guess.

Then, in `issues`, report any remaining problems with severity 'critical'
(an unanswerable question, two defensibly-correct options, a distractor that is
also correct) or 'minor' (wording, register). Also check, as issues:
distractor quality, coherence, whether any vocabulary or grammar exceeds the
CEFR level in the spec (proper nouns excluded), and structure (question count,
option count, approximate word count vs the spec).

Report by calling the `report_review` tool. Do NOT return a corrected section —
correctness is judged by comparing your independent answers to the key.\
"""


def _strip_answer_keys(section: dict[str, Any]) -> dict[str, Any]:
    """Blind-solve view of a section: remove `correct_index` from every
    question_data and drop any `answer_justification`. Deep-copies the parts it
    mutates so the caller's section (which keeps the real key for grading + the
    FIX call) is never altered. INVARIANT: the rendered VERIFY payload must not
    contain the key (AMENDMENT v1.2 §9.4)."""
    out = dict(section)
    qs: list[dict[str, Any]] = []
    for q in section.get("questions") or []:
        if not isinstance(q, dict):
            qs.append(q)
            continue
        q = {kk: vv for kk, vv in q.items() if kk != "answer_justification"}
        qd = q.get("question_data")
        if isinstance(qd, dict):
            q["question_data"] = {kk: vv for kk, vv in qd.items()
                                  if kk != "correct_index"}
        qs.append(q)
    out["questions"] = qs
    return out


def render_verify_spec_user_message(
    section: dict[str, Any], payload: dict[str, Any], *, k: int
) -> str:
    """Spec-mode BLIND-SOLVE verify: STRUCTURE spec + the section with its
    answer key STRIPPED — NO source, NO key, NO per_question hints (§9.4)."""
    structure = (payload.get("spec") or {}).get("structure") or {}
    blind = _strip_answer_keys(section)
    return (
        "SPECIFICATION:\n"
        + json.dumps(structure, ensure_ascii=False, indent=2)
        + "\n\nEXAM SECTION TO SOLVE (the answer key has been REMOVED — work out "
        "each answer yourself from the material):\n"
        + json.dumps(blind, ensure_ascii=False, indent=2)
    )


# AMENDMENT v1.2 §9.5 — the FIX call is the ONLY spec call that sees the real
# answer key. It runs only after a blind-solve round fails; it receives the
# section (WITH key), the problems CODE found, and the structure spec, and
# returns a corrected section via emit_section. Still blind to the source.
SYSTEM_PROMPT_FIX_SPEC = """\
You are the professional item writer correcting a section you wrote. Unlike the
examiner, you ARE shown the intended answer key together with the specific
problems an independent examiner found. Produce a corrected section that
resolves EVERY listed problem.

Keep the structure exactly: the same number of questions, the same number of
options per question, the same word-count range and the same CEFR vocabulary
level. You MAY rewrite the material, the stems, the options, or move which
option is correct — whatever it takes so that each question has exactly ONE
correct option that is clearly supported by the material, and every question is
answerable from the material alone. Return the corrected section via the
`emit_section` tool (1 text material + N multiple-choice questions, each with a
short answer_justification quoting the supporting evidence).\
"""


def render_fix_spec_user_message(
    section: dict[str, Any], payload: dict[str, Any], *, k: int
) -> str:
    """Spec-mode FIX user turn — sees the spec, the problems, and the section
    WITH its real answer key (the only spec call that does). NO source."""
    structure = (payload.get("spec") or {}).get("structure") or {}
    problems = payload.get("fix_problems") or []
    problems_block = ("\n".join(f"- {p}" for p in problems)
                      if problems else "- (none specified)")
    return (
        "STRUCTURE SPEC (preserve exactly):\n"
        + json.dumps(structure, ensure_ascii=False, indent=2)
        + "\n\nPROBLEMS TO FIX:\n" + problems_block
        + "\n\nSECTION TO FIX (includes the intended answer key):\n"
        + json.dumps(section, ensure_ascii=False, indent=2)
    )


VERIFY_SECTION_SPEC_TOOL: dict[str, Any] = {
    "name": "report_review",
    "description": "Report your independent blind solve (one entry per "
                   "question) plus any remaining issues. Do NOT return a "
                   "corrected section.",
    "input_schema": {
        "type": "object",
        "properties": {
            "per_question": {
                "type": "array",
                "description": "REQUIRED — exactly one entry per question, in "
                               "order. Your own answer reached independently, "
                               "with a verbatim evidence quote from the material.",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "examiner_answer_index": {"type": "integer"},
                        "evidence_quote": {"type": "string"},
                    },
                    "required": ["position", "examiner_answer_index",
                                 "evidence_quote"],
                },
            },
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string",
                                     "enum": ["critical", "minor"]},
                        "question_position": {"type": ["integer", "null"]},
                        "problem": {"type": "string"},
                        "fix": {"type": ["string", "null"]},
                    },
                    "required": ["severity", "problem"],
                },
            },
        },
        "required": ["per_question", "issues"],
    },
}


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
    # --- spec mode (v3+) — None on rewrite-only versions -------------------
    spec_mode: bool = False
    system_analyze: Optional[str] = None
    # (payload) -> user message  (the ONLY render that may see the source)
    render_analyze: Optional[Callable[[dict[str, Any]], str]] = None
    # spec-shaped emit_section tool variant (no source-relative wording)
    emit_section_tool: Optional[dict[str, Any]] = None
    # verify tool variant (spec = blind-solve report; None → VERIFY_SECTION_TOOL)
    verify_section_tool: Optional[dict[str, Any]] = None
    # --- spec FIX step (AMENDMENT v1.2 §9.5) — the only key-aware spec call ---
    system_fix: Optional[str] = None
    # (section_with_key, payload, k) -> user message
    render_fix: Optional[Callable[[dict[str, Any], dict[str, Any], int], str]] = None
    fix_section_tool: Optional[dict[str, Any]] = None


PROMPT_VERSIONS: dict[str, PromptVersion] = {
    "v1": PromptVersion(
        name="v1",
        description="Legacy baseline — verify judges the generated section "
                    "alone (no source). Kept selectable as fallback.",
        system_generate=SYSTEM_PROMPT_GENERATE,
        system_verify=SYSTEM_PROMPT_VERIFY,
        render_generate=lambda payload, k: render_generate_user_message(payload, k=k),
        render_verify=lambda section, payload, k: render_verify_user_message(section, payload),
    ),
    "v2": PromptVersion(
        name="v2",
        description="Anti-clone (production default since 2026-06-11) — verify "
                    "also receives the source section + K directive and "
                    "flags/fixes near-copies.",
        system_generate=SYSTEM_PROMPT_GENERATE,  # unchanged: K scale awaits client
        system_verify=SYSTEM_PROMPT_VERIFY_V2,
        render_generate=lambda payload, k: render_generate_user_message(payload, k=k),
        render_verify=lambda section, payload, k: render_verify_user_message_v2(section, payload, k=k),
    ),
    # v3 entry = the SPEC config only. Sections that fail spec eligibility run
    # the rewrite fallback, which reuses the v2 entry at the adapter level
    # while the service still reports prompt_version=v3 + mode=rewrite
    # (docs/exam-gen-v3-spec-mode/ §10.4).
    "v3": PromptVersion(
        name="v3",
        description="Spec mode (K≥3, MC-core) — the source never enters "
                    "generate/verify prompts; content from skill map + topic "
                    "+ diversity seed. K≤2/ineligible sections fall back to "
                    "the v2 rewrite behaviour.",
        system_generate=SYSTEM_PROMPT_GENERATE_SPEC,
        system_verify=SYSTEM_PROMPT_VERIFY_SPEC,
        render_generate=lambda payload, k: render_generate_spec_user_message(payload, k=k),
        render_verify=lambda section, payload, k: render_verify_spec_user_message(section, payload, k=k),
        spec_mode=True,
        system_analyze=SYSTEM_PROMPT_ANALYZE,
        render_analyze=render_analyze_user_message,
        emit_section_tool=EMIT_SECTION_SPEC_TOOL,
        verify_section_tool=VERIFY_SECTION_SPEC_TOOL,
        system_fix=SYSTEM_PROMPT_FIX_SPEC,
        render_fix=lambda section, payload, k: render_fix_spec_user_message(section, payload, k=k),
        fix_section_tool=EMIT_SECTION_SPEC_TOOL,
    ),
}

# Promoted v1 → v2 on 2026-06-11 after A/B (v2 lower weighted-avg overlap in
# 5/6 paired runs; judge with source context catches name-only clones). v1
# stays in the registry as an explicit opt-out (promptVersion: "v1").
DEFAULT_PROMPT_VERSION = "v2"


def get_prompt_version(name: Optional[str] = None) -> PromptVersion:
    """Resolve a prompt version by name (None/'' → default). ValueError if unknown."""
    key = name or DEFAULT_PROMPT_VERSION
    try:
        return PROMPT_VERSIONS[key]
    except KeyError:
        raise ValueError(
            f"Unknown promptVersion {key!r}; allowed: {', '.join(sorted(PROMPT_VERSIONS))}"
        )


# ===========================================================================
# CORE registry (spec mode) — prompts differ per CORE, not per version. The
# orchestration (ANALYZE→leak→generate→shuffle→trigram→verify→FIX→Tầng B) is
# shared; each core supplies its own prompt set + output tool. The adapters
# resolve CORE_PROMPTS[payload["core"]] when pv.spec_mode (else use the v1/v2
# PromptVersion path unchanged). multiple_choice reuses the v3 objects verbatim
# (byte-identical — asserted in tests). See docs/exam-part-presets §mc_cloze.
# ===========================================================================

# ---- mc_cloze prompts (gap-fill: passage with N numbered gaps, each a
# word/phrase MC). DIFF vs MC core: GENERATE/VERIFY/FIX/ANALYZE wording + the
# emit_cloze output tool. SHARED (reused verbatim): render functions
# (render_analyze/generate/verify/fix), EMIT_SKILL_MAP_TOOL,
# VERIFY_SECTION_SPEC_TOOL, blind-solve mechanic, leak check, invariant. ----

SYSTEM_PROMPT_ANALYZE_CLOZE = """\
You are an exam-design analyst for Cambridge English (KET/PET) GAP-FILL (cloze)
tasks. Analyze the source cloze section below and produce an ABSTRACT GAP PROFILE
— what each numbered gap tests — WITHOUT copying any source content.

Rules:
- Do NOT include any sentence, phrase, proper noun, topic, domain or storyline
from the source in your output. The profile must be fully abstract; a reader
must NOT be able to guess what the source was about.
- The `text_genre` field describes ONLY the text form (article/email/notice/
narrative...), narrative voice + register, and tone — NEVER events, locations,
activities, relationships, or why the text was written.
- For EACH gap, classify the TEST POINT using this taxonomy (pick the closest):
preposition / collocation / phrasal_verb / linker_connector / word_form /
verb_form_tense / modal / article_determiner / quantifier / relative_pronoun /
comparison / fixed_expression / vocabulary_in_context.
- For EACH gap also give: the answer's WORD CLASS (noun / verb / adjective /
adverb / preposition / conjunction / determiner / pronoun) and the DISTRACTOR
PATTERN (how the wrong options are built — e.g. "same word class, wrong
collocation", "near-synonym wrong in context", "grammatically impossible here").
- Estimate the CEFR level of the vocabulary and the word count of the passage.

Return your result by calling the `emit_skill_map` tool: put the test point in
`skill_tested`, the word class in `answer_scope`, and the distractor pattern in
`distractor_pattern`, one `per_question` entry per gap.\
"""

SYSTEM_PROMPT_GENERATE_CLOZE = """\
You are a professional item writer for Cambridge English (KET/PET) gap-fill
(cloze). Write ONE complete, brand-new cloze task following the specification in
the user message. You are NOT shown any existing exam — invent all content.

HARD CONSTRAINTS (violating any makes the output unusable):
1. The passage MUST be about the given topic and text genre, and incorporate the
given story elements naturally.
2. Word count within the stated range (count before finalising); vocabulary must
not exceed the stated CEFR level except proper nouns.
3. EXACTLY N gaps (N from the spec). Gap i MUST test the test point listed for
its position in the PER-GAP SPEC; the answer's word class must match.
4. For each gap provide the correct TARGET (one word or a short fixed phrase) and
(L-1) DISTRACTORS (L from the spec): same word class, plausible, but clearly
WRONG in THIS context (wrong collocation / grammar / meaning). Exactly ONE option
fits; distractors must NOT also fit. Vary how distractors are built.
5. In `text`, mark each gap with a SINGLE numbered blank token [[1]], [[2]], …,
[[N]] — one token per gap, each used EXACTLY ONCE, in any order. Do NOT write the
target word in the passage (the gap is a BLANK; the answer lives only in
per_gap.target). Do NOT use underscores or {{...}}.
   CORRECT:   "I went [[1]] a trip [[2]] my family."   (blanks only)
   WRONG:     "I went [[1]]on a trip [[2]]with my family."   (target written in)
   WRONG:     "I went [[1]]on[[1]] a trip..."                (paired/duplicated)

OUTPUT SHAPE (return via the `emit_cloze` tool):
- `text`: the full passage with the N single numbered blank tokens [[1]]..[[N]],
no target words written in.
- `per_gap`: one entry per gap {position, target, distractors:[L-1], reason} in
order 1..N (reason = why the target is right / distractors wrong).
- a fitting `part_label` and student-facing `instructions` (do NOT leave empty).\
"""

EMIT_CLOZE_TOOL: dict[str, Any] = {
    "name": "emit_cloze",
    "description": "Return the brand-new cloze task: a passage with single "
                   "numbered blank tokens [[1]]..[[N]] (no target words written "
                   "in), plus per-gap target + distractors. The system replaces "
                   "the tokens with blanks and builds the options.",
    "input_schema": {
        "type": "object",
        "properties": {
            "part_label": {"type": "string"},
            "instructions": {"type": "string"},
            "text": {"type": "string",
                     "description": "Passage with each gap as a SINGLE numbered "
                                    "blank token [[1]]..[[N]] (each used once); do "
                                    "NOT write the target word into the passage."},
            "per_gap": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "target": {"type": "string"},
                        "distractors": {"type": "array",
                                        "items": {"type": "string"}},
                        "reason": {"type": ["string", "null"]},
                    },
                    "required": ["position", "target", "distractors"],
                },
            },
        },
        "required": ["part_label", "instructions", "text", "per_gap"],
    },
}

SYSTEM_PROMPT_VERIFY_CLOZE = """\
You are an independent Cambridge examiner taking this gap-fill task as a
candidate. You are NOT shown the answer key — for EACH gap, choose the option
that best fits, using only the passage and the options.

For EVERY gap, in order, return one entry in `per_question` with:
- position
- examiner_answer_index: the 0-based option index YOU conclude fits the gap
- evidence_quote: the exact words around the gap that justify your choice (copy
them verbatim; never leave empty).

Then, in `issues`, mark severity 'critical' for any gap where: it is unanswerable;
OR MORE THAN ONE option is also acceptable in the gap (an ambiguous gap); OR a
distractor is in fact correct. Use 'minor' for wording/register/level. Do NOT try
to guess the author's intended word — report the option that actually fits. Call
`report_review`. Do NOT return a corrected task.\
"""

SYSTEM_PROMPT_FIX_CLOZE = """\
You are the item writer correcting a cloze task you wrote. You ARE shown the
intended answer key and the problems an independent examiner found. Produce a
corrected task that resolves EVERY problem: for an ambiguous gap, change the
passage or the offending distractor so the target is the ONLY option that fits;
for a wrong key, fix it. Keep the structure: same number of gaps, same number of
options per gap, same word-count range and CEFR level. Return the corrected task
via the `emit_cloze` tool (text with [[i]]…[[i]] sentinels + per_gap).\
"""


# ---- open_cloze prompts (PET_R_P6 / KET_R_P5). Like mc_cloze BUT the student
# TYPES one word per gap — no options. The author emits an ACCEPT-LIST per gap
# (answer + accepted_alternatives); CODE grades fill_blank against it (case-
# insensitive + trim). DIFF vs mc_cloze: ANALYZE taxonomy (grammar/function
# words), GENERATE/VERIFY/FIX wording + the emit_open_cloze tool, and a verify
# tool whose examiner TYPES a word (string) instead of picking an index — so the
# verify view must strip correct_answers (not correct_index). SHARED: render_
# analyze/generate/fix, EMIT_SKILL_MAP_TOOL, blind-solve mechanic, leak check. --

SYSTEM_PROMPT_ANALYZE_OPEN_CLOZE = """\
You are an exam-design analyst for Cambridge English (KET/PET) OPEN-CLOZE tasks
(the candidate writes ONE word in each gap — there are NO options). Analyze the
source open-cloze section below and produce an ABSTRACT GAP PROFILE — what each
numbered gap tests — WITHOUT copying any source content.

Rules:
- Do NOT include any sentence, phrase, proper noun, topic, domain or storyline
from the source in your output. The profile must be fully abstract; a reader
must NOT be able to guess what the source was about.
- The `text_genre` field describes ONLY the text form (article/email/notice/
narrative...), narrative voice + register, and tone — NEVER events, locations,
activities, relationships, or why the text was written.
- Open cloze tests GRAMMAR and FUNCTION words, not topic vocabulary. For EACH
gap, classify the TEST POINT using this taxonomy (pick the closest):
article_determiner / preposition / auxiliary_verb / modal / pronoun /
relative_pronoun / conjunction_linker / quantifier / comparison /
verb_form_agreement / fixed_expression / phrasal_verb_particle.
- For EACH gap also give: the answer's WORD CLASS (article / preposition /
auxiliary / modal / pronoun / conjunction / determiner / quantifier / adverb /
particle) and, as the DISTRACTOR PATTERN, the kind of WRONG word a candidate
might plausibly type there (e.g. "wrong preposition in this collocation",
"a different auxiliary that breaks agreement") — open cloze has no printed
options, so describe the common candidate ERROR, not a built distractor.
- Estimate the CEFR level of the vocabulary and the word count of the passage.

Return your result by calling the `emit_skill_map` tool: put the test point in
`skill_tested`, the word class in `answer_scope`, and the candidate-error
pattern in `distractor_pattern`, one `per_question` entry per gap.\
"""

SYSTEM_PROMPT_GENERATE_OPEN_CLOZE = """\
You are a professional item writer for Cambridge English (KET/PET) OPEN CLOZE.
Write ONE complete, brand-new open-cloze task following the specification in the
user message. You are NOT shown any existing exam — invent all content.

HARD CONSTRAINTS (violating any makes the output unusable):
1. The passage MUST be about the given topic and text genre, and incorporate the
given story elements naturally.
2. Word count within the stated range (count before finalising); vocabulary must
not exceed the stated CEFR level except proper nouns.
3. EXACTLY N gaps (N from the spec). Gap i MUST test the test point listed for
its position in the PER-GAP SPEC; the answer's word class must match. Open cloze
tests GRAMMAR / FUNCTION words (articles, prepositions, auxiliaries, pronouns,
conjunctions, quantifiers, relatives...). Each answer is EXACTLY ONE word.
4. Each gap must have ONE clearly best answer given the surrounding text. Design
the context so the intended word is forced; then list, in `accepted_alternatives`,
EVERY other single word that a careful candidate could also defensibly write in
that gap (often none, sometimes one or two). Grading accepts the answer OR any
listed alternative (case-insensitive).
5. In `text`, mark each gap with a SINGLE numbered blank token [[1]], [[2]], …,
[[N]] — one token per gap, each used EXACTLY ONCE, in any order. Do NOT write the
answer word in the passage (the gap is a BLANK; the answer lives only in
per_gap.answer). Do NOT use underscores or {{...}}.
   CORRECT:   "I have lived here [[1]] 2019 and I like [[2]] a lot."  (blanks only)
   WRONG:     "I have lived here [[1]]since 2019..."   (answer written in)
   WRONG:     "I have lived here [[1]]since[[1]] 2019..."  (paired/duplicated)

OUTPUT SHAPE (return via the `emit_open_cloze` tool):
- `text`: the full passage with the N single numbered blank tokens [[1]]..[[N]],
no answer words written in.
- `per_gap`: one entry per gap {position, answer (the single best word),
accepted_alternatives:[other acceptable single words, possibly empty], reason}
in order 1..N (reason = why the answer is right / what is tested).
- a fitting `part_label` and student-facing `instructions` (do NOT leave empty).\
"""

EMIT_OPEN_CLOZE_TOOL: dict[str, Any] = {
    "name": "emit_open_cloze",
    "description": "Return the brand-new open-cloze task: a passage with single "
                   "numbered blank tokens [[1]]..[[N]] (no answer words written "
                   "in), plus a per-gap accept-list (answer + acceptable "
                   "alternatives). The system replaces the tokens with blanks and "
                   "builds the fill-in answer key.",
    "input_schema": {
        "type": "object",
        "properties": {
            "part_label": {"type": "string"},
            "instructions": {"type": "string"},
            "text": {"type": "string",
                     "description": "Passage with each gap as a SINGLE numbered "
                                    "blank token [[1]]..[[N]] (each used once); do "
                                    "NOT write the answer word into the passage."},
            "per_gap": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "answer": {"type": "string",
                                   "description": "the single best word for the gap"},
                        "accepted_alternatives": {
                            "type": "array", "items": {"type": "string"},
                            "description": "other acceptable single words (may be "
                                           "empty)"},
                        "reason": {"type": ["string", "null"]},
                    },
                    "required": ["position", "answer"],
                },
            },
        },
        "required": ["part_label", "instructions", "text", "per_gap"],
    },
}

SYSTEM_PROMPT_VERIFY_OPEN_CLOZE = """\
You are an independent Cambridge examiner taking this OPEN-CLOZE task as a
candidate. There are NO options and NO answer key — for EACH gap, WRITE the one
word you think best fits, using only the passage.

For EVERY gap, in order, return one entry in `per_question` with:
- position
- examiner_answer: the single word YOU would write in the gap
- evidence_quote: the exact words around the gap that justify your word (copy
them verbatim; never leave empty).

Then, in `issues`, mark severity 'critical' for any gap where: it is unanswerable;
OR several DIFFERENT words fit equally well so there is no single best answer (an
over-open gap); OR the surrounding grammar is wrong. Use 'minor' for
wording/register/level. Write the word the passage actually calls for — do not
try to guess a hidden intended word. Call `report_review`. Do NOT return a
corrected task.\
"""

VERIFY_OPEN_CLOZE_TOOL: dict[str, Any] = {
    "name": "report_review",
    "description": "Report your independent blind solve (TYPE one word per gap) "
                   "plus any remaining issues. Do NOT return a corrected task.",
    "input_schema": {
        "type": "object",
        "properties": {
            "per_question": {
                "type": "array",
                "description": "REQUIRED — exactly one entry per gap, in order. "
                               "The word YOU would write in each gap, reached "
                               "independently, with a verbatim evidence quote.",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "examiner_answer": {"type": "string"},
                        "evidence_quote": {"type": "string"},
                    },
                    "required": ["position", "examiner_answer", "evidence_quote"],
                },
            },
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string",
                                     "enum": ["critical", "minor"]},
                        "question_position": {"type": ["integer", "null"]},
                        "problem": {"type": "string"},
                        "fix": {"type": ["string", "null"]},
                    },
                    "required": ["severity", "problem"],
                },
            },
        },
        "required": ["per_question", "issues"],
    },
}

SYSTEM_PROMPT_FIX_OPEN_CLOZE = """\
You are the item writer correcting an open-cloze task you wrote. You ARE shown
the intended accept-list per gap and the problems an independent examiner found
(including any word the examiner wrote that the accept-list did not contain).
For EACH problem decide:
- if the examiner's word is ALSO a correct single-word answer in this context,
ADD it to that gap's accept-list (correct_answers);
- if the gap is genuinely over-open (too many unrelated words fit), REWRITE the
surrounding passage so the intended answer is forced, OR replace the gap with a
cleaner test point.
Keep the structure: same number of gaps, each answer ONE word, same word-count
range and CEFR level. Return the corrected task via the `emit_open_cloze` tool
(text with [[1]]..[[N]] blank tokens + per_gap answer/accepted_alternatives).\
"""


def _strip_open_cloze_keys(section: dict[str, Any]) -> dict[str, Any]:
    """Blind-solve view of an open-cloze section: remove `correct_answers` and
    `case_sensitive` from every question_data and drop `answer_justification`.
    For open cloze the ACCEPT-LIST is the answer key, so (unlike _strip_answer_keys,
    which targets MC `correct_index`) it is what must be hidden from the examiner.
    Deep-copies only the parts it mutates."""
    out = dict(section)
    qs: list[dict[str, Any]] = []
    for q in section.get("questions") or []:
        if not isinstance(q, dict):
            qs.append(q)
            continue
        q = {kk: vv for kk, vv in q.items() if kk != "answer_justification"}
        qd = q.get("question_data")
        if isinstance(qd, dict):
            q["question_data"] = {kk: vv for kk, vv in qd.items()
                                  if kk not in ("correct_answers", "case_sensitive")}
        qs.append(q)
    out["questions"] = qs
    return out


def render_verify_open_cloze_user_message(
    section: dict[str, Any], payload: dict[str, Any], k: int
) -> str:
    """Open-cloze BLIND-SOLVE verify: STRUCTURE spec + the section with its
    accept-list STRIPPED — NO source, NO key. The examiner types one word per
    {{gap:N}}."""
    structure = (payload.get("spec") or {}).get("structure") or {}
    blind = _strip_open_cloze_keys(section)
    return (
        "SPECIFICATION:\n"
        + json.dumps(structure, ensure_ascii=False, indent=2)
        + "\n\nOPEN-CLOZE TASK TO SOLVE (no answer key — for each {{gap:N}} in the "
        "passage, write the ONE word that best fits):\n"
        + json.dumps(blind, ensure_ascii=False, indent=2)
    )


@dataclass(frozen=True)
class CorePrompts:
    """Per-core spec prompt set (resolved by the adapters in spec mode)."""
    system_analyze: str
    render_analyze: Callable[[dict[str, Any]], str]
    emit_skill_map_tool: dict[str, Any]
    system_generate: str
    render_generate: Callable[[dict[str, Any], int], str]
    emit_section_tool: dict[str, Any]
    system_verify: str
    render_verify: Callable[[dict[str, Any], dict[str, Any], int], str]
    verify_section_tool: dict[str, Any]
    system_fix: str
    render_fix: Callable[[dict[str, Any], dict[str, Any], int], str]
    fix_section_tool: dict[str, Any]


_V3 = PROMPT_VERSIONS["v3"]  # multiple_choice reuses these objects verbatim

CORE_PROMPTS: dict[str, CorePrompts] = {
    # byte-identical to current v3: same string/function/tool OBJECTS.
    "multiple_choice": CorePrompts(
        system_analyze=_V3.system_analyze, render_analyze=_V3.render_analyze,
        emit_skill_map_tool=EMIT_SKILL_MAP_TOOL,
        system_generate=_V3.system_generate, render_generate=_V3.render_generate,
        emit_section_tool=_V3.emit_section_tool,
        system_verify=_V3.system_verify, render_verify=_V3.render_verify,
        verify_section_tool=_V3.verify_section_tool,
        system_fix=_V3.system_fix, render_fix=_V3.render_fix,
        fix_section_tool=_V3.emit_section_tool,
    ),
    # mc_cloze: new system prompts + emit_cloze tool; renders reused verbatim.
    "mc_cloze": CorePrompts(
        system_analyze=SYSTEM_PROMPT_ANALYZE_CLOZE, render_analyze=_V3.render_analyze,
        emit_skill_map_tool=EMIT_SKILL_MAP_TOOL,
        system_generate=SYSTEM_PROMPT_GENERATE_CLOZE, render_generate=_V3.render_generate,
        emit_section_tool=EMIT_CLOZE_TOOL,
        system_verify=SYSTEM_PROMPT_VERIFY_CLOZE, render_verify=_V3.render_verify,
        verify_section_tool=VERIFY_SECTION_SPEC_TOOL,
        system_fix=SYSTEM_PROMPT_FIX_CLOZE, render_fix=_V3.render_fix,
        fix_section_tool=EMIT_CLOZE_TOOL,
    ),
    # open_cloze: type-the-word gap fill (fill_blank). New system prompts +
    # emit_open_cloze tool + a string-answer verify tool with its OWN verify
    # render (strips the accept-list, not correct_index). analyze/generate/fix
    # renders reused verbatim.
    "open_cloze": CorePrompts(
        system_analyze=SYSTEM_PROMPT_ANALYZE_OPEN_CLOZE, render_analyze=_V3.render_analyze,
        emit_skill_map_tool=EMIT_SKILL_MAP_TOOL,
        system_generate=SYSTEM_PROMPT_GENERATE_OPEN_CLOZE, render_generate=_V3.render_generate,
        emit_section_tool=EMIT_OPEN_CLOZE_TOOL,
        system_verify=SYSTEM_PROMPT_VERIFY_OPEN_CLOZE,
        render_verify=render_verify_open_cloze_user_message,
        verify_section_tool=VERIFY_OPEN_CLOZE_TOOL,
        system_fix=SYSTEM_PROMPT_FIX_OPEN_CLOZE, render_fix=_V3.render_fix,
        fix_section_tool=EMIT_OPEN_CLOZE_TOOL,
    ),
}


def resolve_core_prompts(payload: dict[str, Any]) -> Optional[CorePrompts]:
    """Spec-mode prompt set for this payload's core, or None for non-spec
    versions (v1/v2 keep their PromptVersion path). Defaults to multiple_choice
    when `core` is absent (back-compat with payloads built before the registry)."""
    pv = get_prompt_version(payload.get("prompt_version"))
    if not pv.spec_mode:
        return None
    return CORE_PROMPTS.get(payload.get("core") or "multiple_choice")
