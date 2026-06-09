"""OpenRouter adapter for AI image generation.

OpenAI-compatible: generate/edit use an image-output model (`IMAGE_MODEL`)
with `modalities=["image","text"]`; the image comes back as a base64 data URL
in `message.images`. Verify uses a vision model (`IMAGE_VERIFY_MODEL`) with
OpenAI tool-calling for a structured verdict. See docs/exam-image-generation
§4 + memory ai-via-openrouter.
"""

import base64
import json
import logging
from typing import Any, Optional

from services.ai.image_generator import ImageGenerator
from services.ai import image_prompts

logger = logging.getLogger(__name__)


def _parse_data_url(url: str) -> tuple[bytes, str]:
    """`data:image/png;base64,XXXX` → (bytes, mime). Raise on non-data URL."""
    if not url.startswith("data:"):
        raise RuntimeError("image generation returned a non-data URL")
    header, _, b64 = url.partition(",")
    mime = header[len("data:"):].split(";")[0] or "image/png"
    return base64.b64decode(b64), mime


def _extract_image(response) -> tuple[bytes, str]:
    """Pull the first image (data URL) out of an OpenRouter chat response."""
    raw = response.model_dump()
    try:
        images = raw["choices"][0]["message"].get("images") or []
    except (KeyError, IndexError):
        images = []
    if not images:
        raise RuntimeError("OpenRouter returned no image in the response")
    return _parse_data_url(images[0]["image_url"]["url"])


class OpenRouterImageGenerator(ImageGenerator):
    def __init__(self, settings) -> None:
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — required when IMAGE_PROVIDER=openrouter."
            )
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
        self._model = settings.image_model
        self._verify_model = settings.image_verify_model
        self.usage: dict[str, int] = {"input": 0, "output": 0, "images": 0}

    async def generate_image(
        self, description: str, *, exam_context: Optional[dict] = None
    ) -> tuple[bytes, str]:
        prompt = image_prompts.build_generate_prompt(description, exam_context)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"modalities": ["image", "text"]},
        )
        self._track_usage(response, image=True)
        return _extract_image(response)

    async def edit_image(
        self, source_url: str, description: str, *, exam_context: Optional[dict] = None
    ) -> tuple[bytes, str]:
        instruction = image_prompts.build_edit_instruction(description, exam_context)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": source_url}},
            ]}],
            extra_body={"modalities": ["image", "text"]},
        )
        self._track_usage(response, image=True)
        return _extract_image(response)

    async def verify_image(
        self, image_bytes: bytes, mime: str, description: str
    ) -> dict[str, Any]:
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        tool = image_prompts.VERIFY_IMAGE_TOOL
        response = await self._client.chat.completions.create(
            model=self._verify_model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": image_prompts.build_verify_message(description)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            tools=[{"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["input_schema"],
            }}],
            tool_choice={"type": "function", "function": {"name": tool["name"]}},
        )
        self._track_usage(response, image=False)
        calls = response.choices[0].message.tool_calls
        if not calls:
            raise RuntimeError("OpenRouter verify did not return a tool call")
        return json.loads(calls[0].function.arguments)

    def _track_usage(self, response, *, image: bool) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage["input"] += getattr(usage, "prompt_tokens", 0) or 0
            self.usage["output"] += getattr(usage, "completion_tokens", 0) or 0
        if image:
            self.usage["images"] += 1
