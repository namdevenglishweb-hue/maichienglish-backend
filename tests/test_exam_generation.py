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
    assert prompts.get_prompt_version(None).name == "v2"     # default (promoted 2026-06-11)
    assert prompts.get_prompt_version("v1").name == "v1"     # legacy opt-out stays selectable
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


# --- v3 spec mode (docs/exam-gen-v3-spec-mode/) ----------------------------- #

_SPEC_CTX = {"level": "KET", "skill": "reading", "title": "A day at Zorblat Park"}

_SRC_PASSAGE = (
    "Last summer Mina visited Zorblat Park with her cousin. The famous park "
    "had a wooden bridge, a small lake and a juggling clown. Mina fed the "
    "ducks near the bridge while her cousin photographed the clown show."
)


def _spec_src_section():
    """Plain-MC section that passes every spec-eligibility condition."""
    return {
        "id": "sec-1", "position": 1, "type": "multiple_choice",
        "part_label": "Part 3", "instructions": "Choose the correct answer.",
        "max_audio_plays": None,
        "materials": [{"type": "text", "content": _SRC_PASSAGE}],
        "questions": [{
            "id": f"q{i}", "position": i + 1, "question_type": "multiple_choice",
            "points": 1,
            "question_data": {"stem": f"Question {i + 1} about the park?",
                              "options": [{"text": f"opt{i}{j}"} for j in range(4)],
                              "correct_index": 0},
        } for i in range(3)],
    }


def _spec_ai_section(passage):
    return {
        "part_label": "Part 3", "instructions": "Read the text and choose A-D.",
        "materials": [{"type": "text", "content": passage}],
        "questions": [{
            "question_type": "multiple_choice",
            "question_data": {"stem": f"New question {i}?",
                              "options": [{"text": f"new{i}{j}"} for j in range(4)],
                              "correct_index": 1},
            "answer_justification": "evidence in the new text",
        } for i in range(3)],
    }


_NEW_PASSAGE = (
    "Tom joined the school chess club in autumn. The first tournament made "
    "him nervous, but his coach showed him a simple opening plan and Tom "
    "finally won his last game on a rainy afternoon at the community hall."
)

_CLEAN_SKILL_MAP = {
    "structure": {"exam_level": "KET", "cefr_level": "A2",
                  "text_genre": "narrative", "word_count_range": [20, 60]},
    "per_question": [{"position": i + 1, "skill_tested": "detail",
                      "answer_scope": "single detail",
                      "distractor_pattern": "plausible but contradicted"}
                     for i in range(3)],
    "style_notes": "simple past narration",
}


class FakeSpecGen:
    """Spec-capable fake: analyze + generate + verify, with call counters."""

    def __init__(self, skill_maps=None, passage=_NEW_PASSAGE, verdicts=None):
        self.usage = {"input": 1, "output": 2}
        self.model = "fake-model"
        self._skill_maps = skill_maps or [_CLEAN_SKILL_MAP]
        self._passage = passage
        self._v = verdicts or []
        self.analyze_calls = self.generate_calls = self.verify_calls = 0

    async def analyze_section(self, payload):
        r = self._skill_maps[min(self.analyze_calls, len(self._skill_maps) - 1)]
        self.analyze_calls += 1
        return r

    async def generate_section(self, payload, *, k):
        self.generate_calls += 1
        return _spec_ai_section(self._passage)

    async def verify_section(self, section, payload, *, k):
        self.verify_calls += 1
        if self._v:
            return self._v[min(self.verify_calls - 1, len(self._v) - 1)]
        return {"is_acceptable": True, "issues": []}


class FakeSkillMapCache:
    def __init__(self):
        self.rows: dict[str, tuple[dict, str]] = {}

    async def get(self, section_id, source_hash):
        row = self.rows.get(section_id)
        return row[0] if row and row[1] == source_hash else None

    async def upsert(self, section_id, skill_map, source_hash, model):
        self.rows[section_id] = (skill_map, source_hash)


def test_spec_core_assignment_matrix():
    """Orchestration gate (K, level) + MC-core eligibility — design §3."""
    from services.ai import spec_mode as S

    good = _spec_src_section()
    assert S.assign_core(good, 3, "KET") == "multiple_choice"
    assert S.assign_core(good, 2, "KET") is None          # gate: K < 3
    assert S.assign_core(good, 5, "IELTS") is None        # gate: level
    bad_type = {**good, "type": "multiple_choice_shared"}
    assert S.assign_core(bad_type, 5, "KET") is None      # MC-shared deferred
    two_mats = {**good, "materials": good["materials"] * 2}
    assert S.assign_core(two_mats, 5, "KET") is None      # needs exactly 1
    import copy
    pic = copy.deepcopy(good)
    pic["questions"][0]["question_data"]["options"][0] = {"image_url": "x.png"}
    assert S.assign_core(pic, 5, "KET") is None           # picture-MC
    mixed = copy.deepcopy(good)
    mixed["questions"][1]["question_data"]["options"] = [{"text": "a"}, {"text": "b"}]
    assert S.assign_core(mixed, 5, "KET") is None         # mixed option counts


def test_spec_pure_functions():
    from services.ai import spec_mode as S

    src = _spec_src_section()
    block = S.build_blocklist(src)
    assert "zorblat" in block                  # proper noun mid-sentence
    assert "mina" in block                     # proper noun
    assert "choose" not in block               # instructions are EXCLUDED (N3)
    assert S.find_leaks('{"note": "distractors recycle"}', ["actor"]) == []
    assert S.find_leaks('{"text_genre": "a Zorblat brochure"}', block) == ["zorblat"]

    pct, common = S.trigram_overlap(_SRC_PASSAGE, _SRC_PASSAGE)
    assert pct == 100.0 and common >= 3
    assert S.similarity_violation(_SRC_PASSAGE, _SRC_PASSAGE) is not None
    assert S.similarity_violation(_NEW_PASSAGE, _SRC_PASSAGE) is None

    h1 = S.section_source_hash({"b": 1, "a": [1, 2]})
    h2 = S.section_source_hash({"a": [1, 2], "b": 1})
    assert h1 == h2                            # key-order invariant (N6)

    facts = S.derive_structure_facts(src, "KET")
    assert facts["num_questions"] == 3 and facts["options_per_question"] == 4
    lying_map = {"structure": {"num_questions": 99, "text_genre": "g"}}
    merged = S.merge_structure(lying_map, facts)
    assert merged["structure"]["num_questions"] == 3   # code overrides ANALYZE (N10)
    assert merged["structure"]["text_genre"] == "g"    # qualitative kept

    assert S.word_count_violation("one two three", [10, 60]) is not None
    assert S.word_count_violation(" ".join(["w"] * 30), [20, 60]) is None
    assert S.word_count_violation(" ".join(["w"] * 18), [20, 60]) is None  # ±15%
    # models emit JSON floats for integer fields — guard must NOT be skipped
    assert S.word_count_violation("one two three", [10.0, 60.0]) is not None


async def test_spec_pipeline_happy_and_cache():
    """Full spec path: analyze→leak→facts→seed→generate→checks→verify; report
    carries mode/topic/seed/hash/trigram; 2nd run hits the cache."""
    import random

    cache = FakeSkillMapCache()
    gen = FakeSpecGen()
    section, report = await G.generate_one_section(
        _spec_src_section(), 5, exam_context=_SPEC_CTX, generator=gen,
        rounds=1, prompt_version="v3", rng=random.Random(1),
        skill_map_cache_override=cache)

    assert report["mode"] == "spec" and report["core"] == "multiple_choice"
    assert report["prompt_version"] == "v3"
    assert report["topic"] and report["diversity_seed"]["narrator"]
    assert report["skill_map_hash"] in {v[1] for v in cache.rows.values()}
    assert report["trigram_overlap_pct"] < 10
    assert section["materials"][0]["content"] == _NEW_PASSAGE
    assert section["instructions"] == "Read the text and choose A-D."  # from AI, not source
    assert section["questions"][0]["points"] == 1                      # forced from source
    assert gen.analyze_calls == 1

    gen2 = FakeSpecGen()
    await G.generate_one_section(
        _spec_src_section(), 5, exam_context=_SPEC_CTX, generator=gen2,
        rounds=1, prompt_version="v3", rng=random.Random(2),
        skill_map_cache_override=cache)
    assert gen2.analyze_calls == 0             # cache hit — no re-analyze


def test_spec_invariant_source_never_in_prompts():
    """DoD #1: source text/title never reach spec generate/verify prompts —
    including the retry branch and the admin-topic branch."""
    from services.ai import prompts

    pv = prompts.get_prompt_version("v3")
    payload = {
        "prompt_version": "v3",
        "exam_context": {"level": "KET", "skill": "reading"},  # NO title (B2)
        "spec": _CLEAN_SKILL_MAP, "topic": "a chess club", "genre": "narrative",
        "diversity_seed": {"narrator": "a teenage boy"},
        "retry_error": "trigram overlap 14.2%, 5 common trigrams; limit 10%",
    }
    gen_msg = pv.render_generate(payload, 3)
    verify_msg = pv.render_verify(_spec_ai_section(_NEW_PASSAGE), payload, 3)
    for banned in ("Zorblat", "Mina", "juggling clown", "A day at Zorblat Park"):
        assert banned not in gen_msg and banned not in verify_msg
    assert "PER-QUESTION SPEC" in gen_msg                  # K=3 gets per_question
    assert "PER-QUESTION SPEC" not in pv.render_generate(payload, 5)  # K=5 doesn't
    assert "per_question" not in verify_msg                # verify structure-only (#14)


async def test_spec_analyze_leak_exhausts_budget():
    """Skill map keeps leaking a blocklisted term → ANALYZE_DOMAIN_LEAK after
    1+2 attempts; nothing cached."""
    leaky = {**_CLEAN_SKILL_MAP,
             "style_notes": "a story about the Zorblat attraction"}
    cache = FakeSkillMapCache()
    gen = FakeSpecGen(skill_maps=[leaky])
    with pytest.raises(G.SectionGenerationError, match="ANALYZE_DOMAIN_LEAK"):
        await G.generate_one_section(
            _spec_src_section(), 5, exam_context=_SPEC_CTX, generator=gen,
            rounds=1, prompt_version="v3", skill_map_cache_override=cache)
    assert gen.analyze_calls == 3 and gen.generate_calls == 0
    assert not cache.rows


async def test_spec_trigram_guard_blocks_clones():
    """Generated material = source verbatim → guard trips on every attempt
    (before any verify call) → section fails within the generate budget."""
    cache = FakeSkillMapCache()
    gen = FakeSpecGen(passage=_SRC_PASSAGE)  # clone of the source
    with pytest.raises(G.SectionGenerationError, match="trigram overlap"):
        await G.generate_one_section(
            _spec_src_section(), 5, exam_context=_SPEC_CTX, generator=gen,
            rounds=1, prompt_version="v3", skill_map_cache_override=cache)
    assert gen.generate_calls == 3 and gen.verify_calls == 0  # guard BEFORE verify


async def test_spec_ineligible_falls_back_to_rewrite():
    """v3 + ineligible section (K=2 here) → rewrite path, mode recorded,
    provenance version stays v3."""
    src = _src_section()  # audio section from the older fixtures — ineligible
    _, report = await G.generate_one_section(
        src, 2, exam_context=_CTX, generator=FakeGen([_good_ai()]),
        rounds=1, prompt_version="v3")
    assert report["mode"] == "rewrite"
    assert report["prompt_version"] == "v3"


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
