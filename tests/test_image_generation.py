"""High-level tests for AI image generation core (no DB, mocked gen+storage).

Few + high-level (see memory testing-prefer-high-level): they exercise
`generate_one_image` through its public surface — edit/generate selection +
upload, and the fallback that refuses to upload a rejected image. API-level
coverage (config-gate, RBAC, job lifecycle) lives in
test_image_generation_integration.py.
"""
import pytest

import services.image_generation_service as I


class FakeImageGen:
    def __init__(self, verdicts=None):
        self.usage = {"images": 0}
        self.gen = self.edit = self._vi = 0
        self._v = verdicts or []

    async def generate_image(self, description, *, exam_context=None):
        self.gen += 1
        return (b"PNG", "image/png")

    async def edit_image(self, source_url, description, *, exam_context=None):
        self.edit += 1
        return (b"PNG", "image/png")

    async def verify_image(self, image_bytes, mime, description):
        if self._v:
            r = self._v[min(self._vi, len(self._v) - 1)]
            self._vi += 1
            return r
        return {"is_acceptable": True, "reason": "ok"}


class FakeStore:
    def __init__(self):
        self.calls = []

    async def upload_bytes(self, bucket, content_type, data):
        self.calls.append((bucket, content_type))
        return f"https://x/{bucket}/a.png"


async def test_generate_one_image_generate_and_edit():
    """No source → generate mode; a source URL → edit mode. Both upload once."""
    g, st = FakeImageGen(), FakeStore()
    r = await I.generate_one_image("a cat", generator=g, storage=st, rounds=2)
    assert r["mode"] == "generate" and g.gen == 1 and g.edit == 0
    assert r["image_url"].endswith("a.png") and st.calls == [("images", "image/png")]

    g, st = FakeImageGen(), FakeStore()
    r = await I.generate_one_image("a cat", source_image_url="https://old.png",
                                   generator=g, storage=st)
    assert r["mode"] == "edit" and g.edit == 1 and g.gen == 0


async def test_generate_one_image_fallback_on_verify_fail():
    """Verify rejects every round → raise, and NEVER upload a wrong image."""
    g = FakeImageGen([{"is_acceptable": False, "reason": "wrong number"}])
    st = FakeStore()
    with pytest.raises(I.ImageGenerationError):
        await I.generate_one_image("x", generator=g, storage=st, rounds=2)
    assert st.calls == []
