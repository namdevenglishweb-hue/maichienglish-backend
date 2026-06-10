"""Groq adapter for AI text generation.

Groq exposes an OpenAI-compatible API (`https://api.groq.com/openai/v1`), so
this is a thin config over `OpenAICompatibleGenerator`. The model is a Groq
model id from `AI_MODEL` (e.g. `llama-3.3-70b-versatile`,
`moonshotai/kimi-k2-instruct`, `openai/gpt-oss-120b`). Key: `GROQ_API_KEY`.
"""

from services.ai.adapters.openai_compatible import OpenAICompatibleGenerator


class GroqGenerator(OpenAICompatibleGenerator):
    def __init__(self, settings, *, model=None, max_tokens=None) -> None:
        super().__init__(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            model=model or settings.ai_model,
            max_tokens=max_tokens or settings.ai_max_tokens,
            key_env="GROQ_API_KEY",
            provider="groq",
        )
