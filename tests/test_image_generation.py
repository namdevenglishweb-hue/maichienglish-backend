"""Unit tests for AI image generation core (no DB, mocked generator+storage).

Covers the testcase doc groups that need neither DB nor a real OpenRouter
call: edit/generate selection, vision-verify + retry + fallback, rounds=0,
upload, data-URL parsing, prompts, factory precondition. API/DB cases
(TC-IMG-12..21) are integration (gated).
"""
import base64

import pytest

import services.image_generation_service as I
from services.ai import image_prompts
from services.ai.adapters.openrouter_image import _extract_image, _parse_data_url
from services.ai.image_generator import get_image_generator


class FakeImageGen:
    def __init__(self, verdicts=None):
        self.usage = {"images": 0, "input": 1, "output": 1}
        self.gen = self.edit = self.vi = 0
        self._v = verdicts or []

    async def generate_image(self, description, *, exam_context=None):
        self.gen += 1
        return (b"PNG", "image/png")

    async def edit_image(self, source_url, description, *, exam_context=None):
        self.edit += 1
        return (b"PNG", "image/png")

    async def verify_image(self, image_bytes, mime, description):
        if self._v:
            r = self._v[min(self.vi, len(self._v) - 1)]
            self.vi += 1
            return r
        return {"is_acceptable": True, "reason": "ok"}


class FakeStore:
    def __init__(self):
        self.calls = []

    async def upload_bytes(self, bucket, content_type, data):
        self.calls.append((bucket, content_type, len(data)))
        return f"https://x/{bucket}/abc.png"


# --------------------------------------------------------------------------
# Edit-or-generate (TC-IMG-01/02) + happy
# --------------------------------------------------------------------------

async def test_generate_mode_no_source():
    g, st = FakeImageGen(), FakeStore()
    r = await I.generate_one_image("a cat", generator=g, storage=st, rounds=2)
    assert r["mode"] == "generate" and g.gen == 1 and g.edit == 0
    assert r["rounds"] == 1 and r["image_url"].endswith("abc.png")
    assert st.calls == [("images", "image/png", 3)]


async def test_edit_mode_with_source():
    g, st = FakeImageGen(), FakeStore()
    r = await I.generate_one_image("a cat", source_image_url="https://old.png",
                                   generator=g, storage=st, rounds=2)
    assert r["mode"] == "edit" and g.edit == 1 and g.gen == 0


# --------------------------------------------------------------------------
# Vision-verify + retry + fallback (TC-IMG-04/05/06/07/08)
# --------------------------------------------------------------------------

async def test_verify_pass_first_round():
    g, st = FakeImageGen(), FakeStore()
    r = await I.generate_one_image("x", generator=g, storage=st, rounds=2)
    assert r["rounds"] == 1 and len(st.calls) == 1


async def test_verify_retry_then_pass():
    g = FakeImageGen([{"is_acceptable": False, "reason": "no clock"},
                      {"is_acceptable": True, "reason": "ok"}])
    st = FakeStore()
    r = await I.generate_one_image("clock 9am", generator=g, storage=st, rounds=2)
    assert r["rounds"] == 2 and len(st.calls) == 1  # upload only the accepted one


async def test_verify_always_fail_raises_no_upload():
    g = FakeImageGen([{"is_acceptable": False, "reason": "wrong number"}])
    st = FakeStore()
    with pytest.raises(I.ImageGenerationError) as exc:
        await I.generate_one_image("x", generator=g, storage=st, rounds=2)
    assert exc.value.reason == "wrong number"
    assert st.calls == []  # never upload a rejected image


async def test_rounds_zero_skips_verify():
    g, st = FakeImageGen(), FakeStore()
    r = await I.generate_one_image("x", generator=g, storage=st, rounds=0)
    assert r["rounds"] == 0 and g.vi == 0 and len(st.calls) == 1


async def test_empty_description_rejected():
    with pytest.raises(I.ImageGenerationError):
        await I.generate_one_image("  ", generator=FakeImageGen(), storage=FakeStore())


# --------------------------------------------------------------------------
# Data URL parse / extract (TC-IMG-09/10) + prompts
# --------------------------------------------------------------------------

def test_parse_data_url():
    b64 = base64.b64encode(b"hello").decode()
    data, mime = _parse_data_url(f"data:image/jpeg;base64,{b64}")
    assert data == b"hello" and mime == "image/jpeg"


def test_parse_data_url_rejects_plain_url():
    with pytest.raises(RuntimeError):
        _parse_data_url("https://x/a.png")


def test_extract_image_from_response():
    b64 = base64.b64encode(b"img").decode()

    class R:
        def model_dump(self):
            return {"choices": [{"message": {"images": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}}]}

    data, mime = _extract_image(R())
    assert data == b"img" and mime == "image/png"


def test_extract_image_no_image_raises():
    class R:
        def model_dump(self):
            return {"choices": [{"message": {"content": "no image"}}]}

    with pytest.raises(RuntimeError):
        _extract_image(R())


def test_image_prompts():
    assert image_prompts.VERIFY_IMAGE_TOOL["name"] == "report_image_review"
    p = image_prompts.build_generate_prompt("a beach", {"level": "KET", "skill": "reading"})
    assert "EXACTLY" in p and "beach" in p
    assert "KEEPING the same" in image_prompts.build_edit_instruction("a form")


# --------------------------------------------------------------------------
# Factory precondition (TC-IMG-22/25)
# --------------------------------------------------------------------------

def test_factory_missing_key_raises(monkeypatch):
    monkeypatch.setattr("config.settings.get_settings",
                        lambda: type("S", (), {"image_provider": "openrouter",
                                               "openrouter_api_key": None,
                                               "openrouter_base_url": "x",
                                               "image_model": "m", "image_verify_model": "v"})())
    with pytest.raises(RuntimeError):
        get_image_generator()


def test_factory_unknown_provider(monkeypatch):
    monkeypatch.setattr("config.settings.get_settings",
                        lambda: type("S", (), {"image_provider": "foo"})())
    with pytest.raises(ValueError):
        get_image_generator()
