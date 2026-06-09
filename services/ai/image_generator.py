"""Provider-agnostic AI image generation interface.

Mirrors `generator.py` (text). Default provider `openrouter` (OpenAI-compatible
gateway; image-output model for generate/edit, vision model for verify — both
OpenRouter slugs from settings). See docs/exam-image-generation §2.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class ImageGenerator(ABC):
    """Generate / edit one image, and verify it matches a description.

    `generate_image` / `edit_image` return `(bytes, mime)`. `verify_image`
    returns `{is_acceptable: bool, reason: str}`. Adapters track `usage`.
    """

    usage: dict[str, int]

    @abstractmethod
    async def generate_image(
        self, description: str, *, exam_context: Optional[dict] = None
    ) -> tuple[bytes, str]:
        ...

    @abstractmethod
    async def edit_image(
        self, source_url: str, description: str, *, exam_context: Optional[dict] = None
    ) -> tuple[bytes, str]:
        ...

    @abstractmethod
    async def verify_image(
        self, image_bytes: bytes, mime: str, description: str
    ) -> dict[str, Any]:
        ...


def get_image_generator() -> ImageGenerator:
    """Factory — returns the configured provider adapter (`IMAGE_PROVIDER`)."""
    from config.settings import get_settings

    settings = get_settings()
    provider = settings.image_provider
    if provider == "openrouter":
        from services.ai.adapters.openrouter_image import OpenRouterImageGenerator

        return OpenRouterImageGenerator(settings)
    raise ValueError(f"Unsupported IMAGE_PROVIDER: {provider!r}")
