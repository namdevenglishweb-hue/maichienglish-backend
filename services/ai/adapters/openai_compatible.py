"""Shared base for OpenAI-compatible text providers (OpenRouter, Groq, …).

Any provider that exposes the OpenAI chat-completions + tool-calling API can
reuse this — only `api_key` / `base_url` / `model` differ. Concrete adapters
(openrouter_generator.py, groq_generator.py) just pass those in. Structured
output uses OpenAI tool-calling; the Anthropic-style tool schemas in
`prompts.py` are converted to OpenAI `function` shape here.
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


class OpenAICompatibleGenerator(AIContentGenerator):
    """Text generator over any OpenAI-compatible endpoint."""

    def __init__(
        self, *, api_key: str, base_url: str, model: str, max_tokens: int,
        key_env: str, provider: str, extra_create: dict | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                f"{key_env} is not set — required when AI_PROVIDER={provider}."
            )
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens
        # Provider-specific extra kwargs for chat.completions.create (e.g. Gemini
        # sets reasoning_effort=none so thinking tokens don't leak into the
        # forced function call).
        self._extra_create = extra_create or {}
        self.model = model        # effective model (override or env) — for provenance
        self.provider = provider
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
            **self._extra_create,
        )
        self._track_usage(response)
        choice = response.choices[0]
        calls = choice.message.tool_calls
        if not calls:
            raise RuntimeError(
                f"{self._model} did not return a `{tool['name']}` tool call "
                f"(finish_reason={choice.finish_reason})."
            )
        return json.loads(calls[0].function.arguments)

    def _track_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.usage["input"] += getattr(usage, "prompt_tokens", 0) or 0
        self.usage["output"] += getattr(usage, "completion_tokens", 0) or 0
