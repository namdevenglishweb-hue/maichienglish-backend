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


# --- balanced answer-key shuffle (post-process by code, client §6 port) ---- #

def _mc_section(n=5, n_options=4, type_="multiple_choice", shared_options=None):
    qs = []
    for i in range(n):
        opts = (list(shared_options) if shared_options
                else [{"text": f"opt{i}{j}"} for j in range(n_options)])
        qs.append({
            "question_type": "multiple_choice", "points": 1, "position": i + 1,
            "question_data": {"stem": f"q{i}", "options": opts,
                              "correct_index": i % len(opts)},
        })
    return {"type": type_, "questions": qs}


def test_shuffle_preserves_answers_and_balances_keys_every_run():
    """The §6 property: answers only MOVE (never change), and no position
    holds more than ceil(n/options) keys — on EVERY run, not on average."""
    import copy
    import random

    seen_patterns = set()
    for seed in range(30):
        sec = _mc_section()
        before = copy.deepcopy(sec)
        G.shuffle_answer_keys(sec, rng=random.Random(seed))
        counts = [0, 0, 0, 0]
        for q, bq in zip(sec["questions"], before["questions"]):
            qd, bqd = q["question_data"], bq["question_data"]
            # same answer text, same option multiset — only positions moved
            assert qd["options"][qd["correct_index"]] == bqd["options"][bqd["correct_index"]]
            assert sorted(o["text"] for o in qd["options"]) == \
                   sorted(o["text"] for o in bqd["options"])
            counts[qd["correct_index"]] += 1
        assert max(counts) <= 2  # ceil(5/4)
        seen_patterns.add(tuple(q["question_data"]["correct_index"] for q in sec["questions"]))
    assert len(seen_patterns) > 1  # still random across runs

    # non-4-option edge: 6 questions x 3 options → cap ceil(6/3)=2
    sec = _mc_section(n=6, n_options=3)
    G.shuffle_answer_keys(sec, rng=random.Random(1))
    counts3 = [0, 0, 0]
    for q in sec["questions"]:
        counts3[q["question_data"]["correct_index"]] += 1
    assert max(counts3) <= 2


def test_shuffle_mixed_option_counts_balance_per_group():
    """Mixed 4-option + 2-option questions in one section: balance must hold
    PER option-count group (the old max+modulo approach clustered all
    2-option keys on one position in ~3% of runs)."""
    import copy
    import random

    for seed in range(200):
        sec = {"type": "multiple_choice", "questions": []}
        sec["questions"] += _mc_section(n=2, n_options=4)["questions"]
        sec["questions"] += _mc_section(n=4, n_options=2)["questions"]
        before = copy.deepcopy(sec)
        G.shuffle_answer_keys(sec, rng=random.Random(seed))

        counts2, counts4 = [0, 0], [0, 0, 0, 0]
        for q, bq in zip(sec["questions"], before["questions"]):
            qd, bqd = q["question_data"], bq["question_data"]
            assert qd["options"][qd["correct_index"]] == bqd["options"][bqd["correct_index"]]
            (counts4 if len(qd["options"]) == 4 else counts2)[qd["correct_index"]] += 1
        assert max(counts2) <= 2  # ceil(4/2) — no all-on-one-position clustering
        assert max(counts4) <= 1  # ceil(2/4)


def test_shuffle_shared_section_keeps_one_common_table():
    """multiple_choice_shared: ONE permutation for all questions — the FE
    detects the shared table by identical option lists, so per-question
    shuffling would break the rendering. Answers still preserved."""
    import copy
    import random

    shared = [{"text": x} for x in ("A", "B", "C", "D")]
    sec = _mc_section(type_="multiple_choice_shared", shared_options=shared)
    before = copy.deepcopy(sec)
    G.shuffle_answer_keys(sec, rng=random.Random(7))

    lists = [q["question_data"]["options"] for q in sec["questions"]]
    assert all(l == lists[0] for l in lists)            # still one shared table
    for q, bq in zip(sec["questions"], before["questions"]):
        qd, bqd = q["question_data"], bq["question_data"]
        assert qd["options"][qd["correct_index"]] == bqd["options"][bqd["correct_index"]]


def test_shuffle_never_touches_other_types_or_invalid_questions():
    import copy

    sec = {"type": "fill_blank", "questions": [
        {"question_type": "fill_blank", "points": 1, "position": 1,
         "question_data": {"correct_answers": ["x"], "case_sensitive": False}},
        {"question_type": "matching", "points": 1, "position": 2,  # excluded by request
         "question_data": {"stem": "m", "options": [{"text": "A"}, {"text": "B"}],
                           "correct_index": 0}},
        {"question_type": "multiple_choice", "points": 1, "position": 3,
         "question_data": {"stem": "broken", "options": [{"text": "A"}, {"text": "B"}],
                           "correct_index": 9}},  # invalid index → skipped, not crashed
    ]}
    before = copy.deepcopy(sec)
    G.shuffle_answer_keys(sec)
    assert sec == before


def test_model_catalog_entries_are_valid():
    """Curated catalog stays consistent with the provider registry —
    a typo'd provider here would 500 the FE dropdown or fail every job."""
    from services.ai.catalog import CURATED_MODELS
    from services.ai.generator import KNOWN_PROVIDERS

    assert CURATED_MODELS, "catalog must not be empty"
    seen = set()
    for entry in CURATED_MODELS:
        assert entry["provider"] in KNOWN_PROVIDERS
        assert entry["model"] and entry["label"]
        key = (entry["provider"], entry["model"])
        assert key not in seen, f"duplicate catalog entry {key}"
        seen.add(key)


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
