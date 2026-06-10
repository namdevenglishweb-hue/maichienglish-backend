"""Google Gemini adapter for AI text generation (direct, "real" Gemini).

Gemini exposes an OpenAI-compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai/`), so this is a thin
config over `OpenAICompatibleGenerator` — same pattern as Groq. The model is a
bare Gemini id from `AI_MODEL` (e.g. `gemini-2.5-pro`, `gemini-2.5-flash`) — NOT
the `google/...` OpenRouter slug. Key: `GEMINI_API_KEY`.

Provider value for the FE: `gemini` (direct Google) — distinct from
`openrouter` + `google/gemini-...` (Gemini proxied via OpenRouter).
"""

from services.ai.adapters.openai_compatible import OpenAICompatibleGenerator


class GeminiGenerator(OpenAICompatibleGenerator):
    def __init__(self, settings, *, model=None, max_tokens=None) -> None:
        super().__init__(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            model=model or settings.ai_model,
            max_tokens=max_tokens or settings.ai_max_tokens,
            key_env="GEMINI_API_KEY",
            provider="gemini",
            # Disable thinking: on Gemini's OpenAI-compat endpoint the thinking
            # stream otherwise leaks into the forced function call and corrupts
            # question_data. Verified: gemini-3-flash-preview emits clean output.
            extra_create={"reasoning_effort": "none"},
        )
