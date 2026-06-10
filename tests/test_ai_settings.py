"""High-level unit test for AI-settings resolution (no DB, get_stored mocked).

The tricky bit: a stored 0 (e.g. self_review_rounds=0 to disable verify for
Gemini) must override the env default, while a stored NULL must fall back to it.
API behaviour is covered in test_ai_settings_integration.py.
"""
from services.ai_settings_service import AISettingsService


async def test_get_effective_resolution(monkeypatch):
    from config.settings import get_settings
    s = get_settings()
    svc = AISettingsService()

    async def _no_row():
        return None

    monkeypatch.setattr(svc, "get_stored", _no_row)
    eff = await svc.get_effective()
    assert eff["provider"] == s.ai_provider
    assert eff["self_review_rounds"] == s.ai_self_review_rounds

    async def _partial():
        # model overridden, self_review_rounds=0 (must win over env), rest NULL
        return {"provider": None, "model": "X", "max_tokens": None, "self_review_rounds": 0}

    monkeypatch.setattr(svc, "get_stored", _partial)
    eff = await svc.get_effective()
    assert eff["model"] == "X"               # explicit override
    assert eff["self_review_rounds"] == 0    # 0 kept, not treated as unset
    assert eff["provider"] == s.ai_provider  # NULL → env default
    assert eff["max_tokens"] == s.ai_max_tokens
