"""Unit tests for AI exam generation core (no DB, mocked AI).

Covers the testcase doc (docs/exam-ai-generation/exam-ai-generation-testcases.md)
groups that need neither a database nor a real Claude call:
structural-invariant checker, merge, K validation, the generate_one_section
pipeline (self-review + retry + budget), media-meta precondition + strip.
Integration (DB) + real-API cases (TC-AIGEN-32+/94+) live elsewhere / are
gated by MAICHI_TEST_DB and an API key.
"""
import pytest

from services.exceptions import ValidationError
from utils.grading_utils import strip_material_meta
import services.exam_generation_service as G
from services.section_type_prompt_service import ALLOWED_TYPES


# --------------------------------------------------------------------------
# Fixtures / fakes
# --------------------------------------------------------------------------

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


_BAD_OPT_AI = {
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


# --------------------------------------------------------------------------
# 1. Structural-invariant checker (TC-AIGEN-01..13)
# --------------------------------------------------------------------------

def test_checker_pass_when_structure_matches():
    s = _src_section()
    G._assert_structure_preserved(s, s)  # no raise


def test_checker_rejects_question_count_change():
    s = _src_section()
    g = {**s, "questions": []}
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, g)


def test_checker_rejects_option_count_change():
    s = _src_section()
    g = {**s, "questions": [{**s["questions"][0],
         "question_data": {"options": [{"text": "A"}], "correct_index": 0}}]}
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, g)


def test_checker_rejects_question_type_change():
    s = _src_section()
    g = {**s, "questions": [{**s["questions"][0], "question_type": "fill_blank"}]}
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, g)


def test_checker_rejects_audio_url_change():
    s = _src_section()
    g = {**s, "materials": [{"type": "audio", "url": "DIFFERENT"}]}
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, g)


def test_checker_allows_meta_change_same_url():
    s = _src_section()
    g = {**s, "materials": [{"type": "audio", "url": "AUDIO",
                             "meta": {"transcript": "new", "pendingReplacement": True}}]}
    G._assert_structure_preserved(s, g)  # no raise — meta may change


def test_checker_rejects_correct_index_out_of_range():
    s = _src_section()
    g = {**s, "questions": [{**s["questions"][0],
         "question_data": {"options": [{"text": "A"}, {"text": "B"}], "correct_index": 2}}]}
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, g)


def test_checker_allows_moved_correct_index():
    s = _src_section()
    g = {**s, "questions": [{**s["questions"][0],
         "question_data": {"options": [{"text": "A"}, {"text": "B"}], "correct_index": 1}}]}
    G._assert_structure_preserved(s, g)  # no raise


def test_checker_rejects_type_or_audio_cap_change():
    s = _src_section()
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, {**s, "max_audio_plays": 99})


def test_checker_rejects_gap_count_change():
    s = {**_src_section(),
         "materials": [{"type": "text", "content": "a {{gap:1}}"}],
         "type": "fill_blank"}
    g = {**s, "materials": [{"type": "text", "content": "a {{gap:1}} {{gap:2}}"}]}
    with pytest.raises(G.StructureMismatch):
        G._assert_structure_preserved(s, g)


# --------------------------------------------------------------------------
# 2. Merge (TC-AIGEN-27 + invariant forcing)
# --------------------------------------------------------------------------

def test_merge_forces_url_and_sets_pending_replacement():
    merged, just = G._merge_generated_section(_src_section(), _good_ai())
    assert merged["materials"][0]["url"] == "AUDIO"  # forced from source
    assert merged["materials"][0]["meta"] == {"transcript": "NEW transcript",
                                              "pendingReplacement": True}
    assert merged["type"] == "multiple_choice" and merged["max_audio_plays"] == 2
    assert merged["questions"][0]["question_type"] == "multiple_choice"
    assert just == [{"position": 1, "justification": "evidence in transcript"}]


def test_merge_drops_answer_justification_from_question_data():
    merged, _ = G._merge_generated_section(_src_section(), _good_ai())
    assert "answer_justification" not in merged["questions"][0]["question_data"]


def test_merge_rejects_wrong_question_count():
    ai = {**_good_ai(), "questions": []}
    with pytest.raises(G.StructureMismatch):
        G._merge_generated_section(_src_section(), ai)


# --------------------------------------------------------------------------
# 3. K validation (TC-AIGEN-15/16)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("k", [0, 6, -1])
def test_k_out_of_range_rejected(k):
    with pytest.raises(ValidationError):
        G._validate_k(k)


@pytest.mark.parametrize("k", ["3", 2.5, True])
def test_k_non_int_rejected(k):
    with pytest.raises(ValidationError):
        G._validate_k(k)


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5])
def test_k_valid(k):
    G._validate_k(k)


# --------------------------------------------------------------------------
# 4. generate_one_section pipeline (TC-AIGEN-17..28)
# --------------------------------------------------------------------------

async def test_generate_one_section_happy():
    sec, rep = await G.generate_one_section(
        _src_section(), 3, exam_context=_CTX, generator=FakeGen([_good_ai()]), rounds=2)
    assert sec["materials"][0]["meta"]["pendingReplacement"] is True
    assert sec["questions"][0]["question_data"]["correct_index"] == 1
    assert rep["self_review"]["rounds"] == 1


async def test_generate_one_section_retry_then_pass():
    sec, _ = await G.generate_one_section(
        _src_section(), 1, exam_context=_CTX,
        generator=FakeGen([_BAD_OPT_AI, _good_ai()]), rounds=0)
    assert sec["questions"][0]["question_data"]["correct_index"] == 1


async def test_generate_one_section_budget_exhausted():
    with pytest.raises(G.SectionGenerationError):
        await G.generate_one_section(
            _src_section(), 1, exam_context=_CTX,
            generator=FakeGen([_BAD_OPT_AI]), rounds=0)


async def test_generate_one_section_self_review_critical_fails():
    crit = [{"is_acceptable": False,
             "issues": [{"severity": "critical", "problem": "wrong answer"}]}]
    with pytest.raises(G.SectionGenerationError):
        await G.generate_one_section(
            _src_section(), 1, exam_context=_CTX,
            generator=FakeGen([_good_ai()], crit), rounds=2)


async def test_generate_one_section_minor_does_not_fail():
    minor = [{"is_acceptable": False,
              "issues": [{"severity": "minor", "problem": "wording"}]}]
    sec, rep = await G.generate_one_section(
        _src_section(), 1, exam_context=_CTX,
        generator=FakeGen([_good_ai()], minor), rounds=1)
    assert sec is not None
    assert rep["self_review"]["final_issues"][0]["severity"] == "minor"


async def test_self_review_applies_fixed_section():
    # Round 1: critical + a fixed_section that's actually good; round 2: accept.
    fixed = _good_ai()
    verdicts = [
        {"is_acceptable": False,
         "issues": [{"severity": "critical", "problem": "x"}], "fixed_section": fixed},
        {"is_acceptable": True, "issues": []},
    ]
    # generate returns a bad-option output, but the fix repairs it.
    sec, rep = await G.generate_one_section(
        _src_section(), 1, exam_context=_CTX,
        generator=FakeGen([_BAD_OPT_AI], verdicts), rounds=2)
    assert rep["self_review"]["rounds"] == 2
    assert sec["questions"][0]["question_data"]["correct_index"] == 1


# --------------------------------------------------------------------------
# 5. Media-meta precondition (TC-AIGEN-52..55) + strip (TC-AIGEN-58..61)
# --------------------------------------------------------------------------

def test_precondition_rejects_audio_without_transcript():
    s = {"position": 1, "materials": [{"type": "audio", "url": "u"}]}
    with pytest.raises(ValidationError):
        G._assert_source_media_meta([s])


def test_precondition_rejects_image_without_description():
    s = {"position": 1, "materials": [{"type": "image", "url": "u"}]}
    with pytest.raises(ValidationError):
        G._assert_source_media_meta([s])


def test_precondition_passes_with_meta_and_text_only():
    ok_media = {"position": 1, "materials": [
        {"type": "audio", "url": "u", "meta": {"transcript": "t"}},
        {"type": "image", "url": "i", "meta": {"description": "d"}}]}
    text_only = {"position": 2, "materials": [{"type": "text", "content": "hi"}]}
    G._assert_source_media_meta([ok_media, text_only])  # no raise


def test_strip_material_meta_removes_meta():
    mats = [{"type": "audio", "url": "u", "meta": {"transcript": "secret"}},
            {"type": "text", "content": "x"}]
    out = strip_material_meta(mats)
    assert "meta" not in out[0]
    assert out[1] == {"type": "text", "content": "x"}
    assert "meta" in mats[0]  # original not mutated


def test_strip_material_meta_handles_non_list():
    assert strip_material_meta(None) == []


def test_media_todos_uses_array_position_not_missing_key():
    # Merged sections (from _merge_generated_section) have NO "position" key;
    # media_todos must derive section_position from array order (1..N).
    sections = [
        {"materials": [{"type": "text", "content": "x"}]},          # no media
        {"materials": [{"type": "audio", "url": "u",
                        "meta": {"transcript": "t", "pendingReplacement": True}}]},
        {"materials": [{"type": "image", "url": "i",
                        "meta": {"description": "d", "pendingReplacement": True}},
                       {"type": "audio", "url": "u2", "meta": {"pendingReplacement": False}}]},
    ]
    todos = G._media_todos(sections)
    assert todos == [
        {"section_position": 2, "material_index": 0, "media_type": "audio"},
        {"section_position": 3, "material_index": 0, "media_type": "image"},
    ]  # section 1 (no media) + the non-pending audio in section 3 excluded


# --------------------------------------------------------------------------
# 5b. Position normalization — non-contiguous source positions + gap remap
# (prevents create_exam_nested rejecting a generated fill_blank section)
# --------------------------------------------------------------------------

def test_normalize_renumbers_positions_and_remaps_gaps():
    section = {
        "type": "fill_blank",
        "materials": [{"type": "text", "content": "Name {{gap:1}} age {{gap:3}}"}],
        "questions": [
            {"position": 1, "question_type": "fill_blank",
             "question_data": {"correct_answers": ["x"]}},
            {"position": 3, "question_type": "fill_blank",
             "question_data": {"correct_answers": ["y"]}},
        ],
    }
    G._normalize_section_positions(section)
    assert [q["position"] for q in section["questions"]] == [1, 2]
    assert section["materials"][0]["content"] == "Name {{gap:1}} age {{gap:2}}"


def test_normalize_noop_when_already_contiguous():
    section = {
        "type": "fill_blank",
        "materials": [{"type": "text", "content": "a {{gap:1}} b {{gap:2}}"}],
        "questions": [{"position": 1, "question_type": "fill_blank",
                       "question_data": {"correct_answers": ["x"]}},
                      {"position": 2, "question_type": "fill_blank",
                       "question_data": {"correct_answers": ["y"]}}],
    }
    G._normalize_section_positions(section)
    assert section["materials"][0]["content"] == "a {{gap:1}} b {{gap:2}}"


# --------------------------------------------------------------------------
# 6. Config sanity (TC-AIGEN-71/88)
# --------------------------------------------------------------------------

def test_form_completion_in_allowed_types():
    assert "form_completion" in ALLOWED_TYPES
    assert len(ALLOWED_TYPES) == 7
