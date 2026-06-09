"""OpenRouter adapter for AI text generation (exam generation).

OpenRouter is OpenAI-compatible, so we use the official `openai` SDK pointed
at OpenRouter's base URL with the OpenRouter key. The model is an OpenRouter
slug (`anthropic/...`, `google/...`) from `AI_MODEL` — never hard-coded.
Structured output uses OpenAI tool-calling (the Anthropic-style tool schemas
in `prompts.py` are converted to OpenAI `function` shape). See
docs/exam-ai-generation + memory ai-via-openrouter.
"""

import json
import logging
from typing import Any

from services.ai.generator import AIContentGenerator
from services.ai import prompts

logger = logging.getLogger(__name__)


def _as_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic-style {name, description, input_schema} → OpenAI function tool."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


class OpenRouterGenerator(AIContentGenerator):
    def __init__(self, settings) -> None:
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — required when AI_PROVIDER=openrouter."
            )
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
        self._model = settings.ai_model
        self._max_tokens = settings.ai_max_tokens
        self.usage: dict[str, int] = {"input": 0, "output": 0}

    async def generate_section(self, payload: dict[str, Any], *, k: int) -> dict[str, Any]:
        return await self._call_tool(
            system_prompt=prompts.SYSTEM_PROMPT_GENERATE,
            user_message=prompts.render_generate_user_message(payload, k=k),
            tool=prompts.EMIT_SECTION_TOOL,
        )

    async def verify_section(
        self, section: dict[str, Any], payload: dict[str, Any], *, k: int
    ) -> dict[str, Any]:
        return await self._call_tool(
            system_prompt=prompts.SYSTEM_PROMPT_VERIFY,
            user_message=prompts.render_verify_user_message(section, payload),
            tool=prompts.VERIFY_SECTION_TOOL,
        )

    async def _call_tool(
        self, *, system_prompt: str, user_message: str, tool: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[_as_openai_tool(tool)],
            tool_choice={"type": "function", "function": {"name": tool["name"]}},
        )
        self._track_usage(response)
        choice = response.choices[0]
        calls = choice.message.tool_calls
        if not calls:
            raise RuntimeError(
                f"OpenRouter did not return a `{tool['name']}` tool call "
                f"(finish_reason={choice.finish_reason})."
            )
        return json.loads(calls[0].function.arguments)

    def _track_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.usage["input"] += getattr(usage, "prompt_tokens", 0) or 0
        self.usage["output"] += getattr(usage, "completion_tokens", 0) or 0
