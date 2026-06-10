"""OpenRouter adapter for AI text generation.

OpenRouter is OpenAI-compatible, so this is a thin config over
`OpenAICompatibleGenerator` — the model is an OpenRouter slug (`anthropic/...`,
`google/...`) from `AI_MODEL`. See docs/exam-ai-generation + memory
ai-via-openrouter.
"""

from services.ai.adapters.openai_compatible import OpenAICompatibleGenerator


class OpenRouterGenerator(OpenAICompatibleGenerator):
    def __init__(self, settings) -> None:
        super().__init__(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            model=settings.ai_model,
            max_tokens=settings.ai_max_tokens,
            key_env="OPENROUTER_API_KEY",
            provider="openrouter",
        )
