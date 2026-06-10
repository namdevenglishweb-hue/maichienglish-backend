"""Provider-agnostic AI content generation interface for exam generation.

Holds the abstract `AIContentGenerator` ABC plus the `get_ai_generator()`
factory (driven by `AI_PROVIDER`). Mirrors the storage adapter pattern
(`storage_service.py` + `adapters/`): business code never imports a
concrete provider; swapping provider/model is env-only. See
`docs/exam-ai-generation/exam-ai-generation-design.md` §2.3 + §12.
"""

from abc import ABC, abstractmethod
from typing import Any


class AIContentGenerator(ABC):
    """One section in → one rewritten/verified section out.

    Both calls return the parsed tool input as a plain dict. The caller
    (exam_generation_service) is responsible for re-validating the shape
    and enforcing structural invariants — the model is never trusted.
    """

    @abstractmethod
    async def generate_section(self, payload: dict[str, Any], *, k: int) -> dict[str, Any]:
        """Rewrite one section's content per K + admin prompts.

        `payload` is built by `services.ai.prompts.build_section_payload`
        and carries: exam_context, the source section (with answers +
        media meta), the per-type prompt (A) and per-section prompt (B).

        Returns the model's `emit_section` tool input: at least
        `{materials: [...], questions: [...]}` (+ optional part_label/
        instructions). Media `url`/`type`, question `question_type`/
        `points` and section `type`/`max_audio_plays` are re-imposed from
        the source by the caller — not trusted from the model.
        """
        ...

    @abstractmethod
    async def verify_section(
        self, section: dict[str, Any], payload: dict[str, Any], *, k: int
    ) -> dict[str, Any]:
        """Independent judge pass over a generated section (design §7).

        Returns the `report_review` tool input:
        `{is_acceptable: bool, issues: [...], fixed_section?: {...}}`.
        """
        ...


def get_ai_generator() -> AIContentGenerator:
    """Factory — returns the configured provider adapter (`AI_PROVIDER`).

    `openrouter` (default, gateway with many models via slug), `groq` (direct,
    OpenAI-compatible), or `anthropic` (direct SDK). Adding another
    OpenAI-compatible provider = a thin subclass of OpenAICompatibleGenerator
    + a branch here.
    """
    from config.settings import get_settings

    settings = get_settings()
    provider = settings.ai_provider
    if provider == "openrouter":
        from services.ai.adapters.openrouter_generator import OpenRouterGenerator

        return OpenRouterGenerator(settings)
    if provider == "groq":
        from services.ai.adapters.groq_generator import GroqGenerator

        return GroqGenerator(settings)
    if provider == "anthropic":
        from services.ai.adapters.anthropic_generator import AnthropicGenerator

        return AnthropicGenerator(settings)
    raise ValueError(f"Unsupported AI_PROVIDER: {provider!r}")
