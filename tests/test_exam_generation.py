"""High-level tests for AI exam generation (no DB, mocked AI).

Intentionally few + high-level (see memory testing-prefer-high-level): they
exercise the whole `generate_one_section` pipeline (AI → merge → self-review →
structural validation) through the public surface, plus the source precondition
and the per-request model override. API-level coverage lives in
test_exam_generation_integration.py. Granular per-helper unit tests were
removed on purpose — they slowed iteration and broke on every refactor.
"""
import pytest

import services.exam_generation_service as G
from services.exceptions import ValidationError


# --- fakes ---------------------------------------------------------------- #

def _src_section():
    return {
        "id": "s1", "exam_id": "e1", "position": 1, "type": "multiple_choice",
        "part_label": "Part 1", "instructions": "Choose.", "max_audio_plays": 2,
        "materials": [{
            "type": "audio", "url": "AUDIO", "label": "",
            "meta": {"transcript": "old transcript", "pendingReplacement": False},
        }],
        "questions": [{
            "id": "q1", "position": 1, "question_type": "multiple_choice", "points": 1,
            "question_data": {"stem": "Q?", "options": [{"text": "A"}, {"text": "B"}],
                              "correct_index": 0},
        }],
    }


def _good_ai():
    """AI output that preserves structure (same #materials/#questions/options)."""
    return {
        "part_label": "Part 1", "instructions": "Choose.",
        "materials": [{"type": "audio", "meta": {"transcript": "NEW transcript"}}],
        "questions": [{
            "question_type": "multiple_choice",
            "question_data": {"stem": "Q2?", "options": [{"text": "C"}, {"text": "D"}],
                              "correct_index": 1},
            "answer_justification": "evidence in transcript",
        }],
    }


_BAD_AI = {  # wrong option count → structure mismatch on every attempt
    "materials": [{"type": "audio", "meta": {"transcript": "N"}}],
    "questions": [{"question_type": "multiple_choice",
                   "question_data": {"stem": "x", "options": [{"text": "C"}],
                                     "correct_index": 0}}],
}

_CTX = {"level": "KET", "skill": "reading", "title": "X"}


class FakeGen:
    def __init__(self, outputs, verdicts=None):
        self.usage = {"input": 1, "output": 2}
        self._o, self._i = outputs, 0
        self._v, self._vi = verdicts or [], 0

    async def generate_section(self, payload, *, k):
        r = self._o[min(self._i, len(self._o) - 1)]
        self._i += 1
        return r

    async def verify_section(self, section, payload, *, k):
        if self._v:
            r = self._v[min(self._vi, len(self._v) - 1)]
            self._vi += 1
            return r
        return {"is_acceptable": True, "issues": []}


# --- the pipeline (happy + abort) ----------------------------------------- #

async def test_generate_one_section_happy():
    """AI content is merged onto the source with all hard invariants re-forced."""
    src = _src_section()
    section, report = await G.generate_one_section(
        src, 3, exam_context=_CTX, generator=FakeGen([_good_ai()]), rounds=1)

    q = section["questions"][0]
    assert q["question_type"] == "multiple_choice"          # forced from source
    assert q["question_data"]["stem"] == "Q2?"              # new AI content kept
    m = section["materials"][0]
    assert m["url"] == "AUDIO"                               # media url forced
    assert m["meta"]["transcript"] == "NEW transcript"      # new transcript kept
    assert m["meta"]["pendingReplacement"] is True          # flagged for media regen
    assert report["self_review"]["rounds"] == 1


async def test_generate_one_section_aborts_on_bad_structure():
    """Structurally-wrong AI output is retried, then raises (never silently saved)."""
    with pytest.raises(G.SectionGenerationError):
        await G.generate_one_section(
            _src_section(), 3, exam_context=_CTX, generator=FakeGen([_BAD_AI]), rounds=0)


# --- source precondition (FE gets a sync 400) ----------------------------- #

def test_media_meta_precondition_rejects_missing_transcript():
    src = _src_section()
    src["materials"][0]["meta"] = {}  # audio source with no transcript
    with pytest.raises(ValidationError):
        G._assert_source_media_meta([src])


# --- per-request model/provider override ---------------------------------- #

class _StubSettings:
    ai_provider = "openrouter"
    ai_model = "env-model"
    ai_max_tokens = 1000
    openrouter_api_key = "fake-key"
    openrouter_base_url = "http://example.invalid/v1"
    groq_api_key = "fake-key"
    groq_base_url = "http://example.invalid/v1"


def test_model_override_routes_and_records(monkeypatch):
    from services.ai.generator import get_ai_generator
    monkeypatch.setattr("config.settings.get_settings", lambda: _StubSettings())

    # override picks the provider + model; default falls back to env
    g = get_ai_generator(provider="groq", model="override-model")
    assert type(g).__name__ == "GroqGenerator"
    assert g.model == "override-model" and g.provider == "groq"
    assert get_ai_generator().model == "env-model"  # env default

    # provenance meta records the ACTUAL model used (not env)
    meta = G._build_meta("src", 2, g, {}, {"media_todos": [], "self_review": {}})
    assert meta["model"] == "override-model" and meta["provider"] == "groq"


# --- prompt-version registry (v1 default; v2 verify sees source + K) ------- #

def test_prompt_version_registry_resolves_and_rejects():
    from services.ai import prompts
    assert prompts.get_prompt_version(None).name == "v1"     # default
    assert prompts.get_prompt_version("v2").name == "v2"
    with pytest.raises(ValueError):
        prompts.get_prompt_version("v999")


def test_v2_verify_message_includes_source_and_k_but_v1_does_not():
    """The whole point of v2: the judge can see what it's comparing against."""
    from services.ai import prompts
    payload = prompts.build_section_payload(
        _src_section(), _CTX, prompt_version="v2")
    generated = {"materials": [], "questions": []}

    v2_msg = prompts.get_prompt_version("v2").render_verify(generated, payload, 3)
    assert "SOURCE SECTION" in v2_msg
    assert "old transcript" in v2_msg                        # source content present
    assert prompts.K_INSTRUCTIONS[3] in v2_msg               # K directive present

    v1_msg = prompts.get_prompt_version("v1").render_verify(generated, payload, 3)
    assert "old transcript" not in v1_msg                    # v1 unchanged (baseline)


async def test_pipeline_records_version_and_shadow_overlap():
    """generate_one_section threads promptVersion + reports the overlap metric."""
    src = _src_section()
    _, report = await G.generate_one_section(
        src, 3, exam_context=_CTX, generator=FakeGen([_good_ai()]),
        rounds=1, prompt_version="v2")
    assert report["prompt_version"] == "v2"
    ov = report["verbatim_overlap"]
    assert 0.0 <= ov["weighted_avg"] <= ov["max"] <= 1.0
    assert ov["fields"]                                      # per-field breakdown

    with pytest.raises(ValidationError):                     # unknown version → 400
        await G.generate_one_section(
            src, 3, exam_context=_CTX, generator=FakeGen([_good_ai()]),
            rounds=0, prompt_version="v999")


def test_verbatim_overlap_metric_separates_copy_from_rewrite():
    """1.0 on a verbatim copy, low on a genuine rewrite; gap markers ignored."""
    src = {
        "materials": [{"type": "text",
                       "content": "Tom plays {{gap:1}} football every sunny weekend afternoon"}],
        "questions": [{"question_type": "multiple_choice",
                       "question_data": {"stem": "What does Tom play?",
                                         "options": [{"text": "football"}, {"text": "tennis"}]}}],
    }
    copy = {
        "materials": [{"type": "text",
                       "content": "Tom plays {{gap:1}} football every sunny weekend afternoon"}],
        "questions": src["questions"],
    }
    rewrite = {
        "materials": [{"type": "text",
                       "content": "Mai practises {{gap:1}} badminton at the city hall on Mondays"}],
        "questions": [{"question_type": "multiple_choice",
                       "question_data": {"stem": "Where does Mai practise?",
                                         "options": [{"text": "city hall"}, {"text": "school gym"}]}}],
    }
    assert G.compute_verbatim_overlap(src, copy)["max"] == 1.0
    assert G.compute_verbatim_overlap(src, rewrite)["max"] < 0.3
