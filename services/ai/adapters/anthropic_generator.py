"""Anthropic (Claude) adapter for AI exam generation.

Wraps the official `anthropic` SDK's AsyncAnthropic client. Forces
structured output via tool-use (`tool_choice`) and caches the (stable)
system prompt on the provider side. Model + token cap come from settings
(`AI_MODEL`, `AI_MAX_TOKENS`) — never hard-coded, so the model is swapped
by env alone. See design §12.
"""

import logging
from typing import Any

from services.ai.generator import AIContentGenerator
from services.ai import prompts

logger = logging.getLogger(__name__)


class AnthropicGenerator(AIContentGenerator):
    def __init__(self, settings, *, model=None, max_tokens=None) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — required when AI_PROVIDER=anthropic."
            )
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = model or settings.ai_model
        self._max_tokens = max_tokens or settings.ai_max_tokens
        self.model = self._model        # effective model — for provenance
        self.provider = "anthropic"
        # Cumulative token usage across this generator's lifetime (one run).
        self.usage: dict[str, int] = {"input": 0, "output": 0}

    # ------------------------------------------------------------------
    # AIContentGenerator
    # ------------------------------------------------------------------

    async def generate_section(self, payload: dict[str, Any], *, k: int) -> dict[str, Any]:
        pv = prompts.get_prompt_version(payload.get("prompt_version"))
        return await self._call_tool(
            system_prompt=pv.system_generate,
            user_message=pv.render_generate(payload, k),
            tool=prompts.EMIT_SECTION_TOOL,
        )

    async def verify_section(
        self, section: dict[str, Any], payload: dict[str, Any], *, k: int
    ) -> dict[str, Any]:
        pv = prompts.get_prompt_version(payload.get("prompt_version"))
        return await self._call_tool(
            system_prompt=pv.system_verify,
            user_message=pv.render_verify(section, payload, k),
            tool=prompts.VERIFY_SECTION_TOOL,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _call_tool(
        self, *, system_prompt: str, user_message: str, tool: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user_message}],
        )
        self._track_usage(response)
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
                return dict(block.input)
        raise RuntimeError(
            f"Claude did not return a `{tool['name']}` tool call "
            f"(stop_reason={response.stop_reason})."
        )

    def _track_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.usage["input"] += getattr(usage, "input_tokens", 0) or 0
        self.usage["output"] += getattr(usage, "output_tokens", 0) or 0
