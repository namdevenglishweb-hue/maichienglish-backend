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
        request_timeout: float | None = None, max_retries: int | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                f"{key_env} is not set — required when AI_PROVIDER={provider}."
            )
        from openai import AsyncOpenAI

        # Per-request timeout + bounded retries (hardening): the SDK default is
        # 600s/request, which stalled the A/B run. None ⇒ fall back to settings.
        if request_timeout is None or max_retries is None:
            from config.settings import get_settings
            s = get_settings()
            request_timeout = s.ai_request_timeout if request_timeout is None else request_timeout
            max_retries = s.ai_max_retries if max_retries is None else max_retries
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            timeout=request_timeout, max_retries=max_retries,
        )
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
        pv = prompts.get_prompt_version(payload.get("prompt_version"))
        return await self._call_tool(
            system_prompt=pv.system_generate,
            user_message=pv.render_generate(payload, k),
            tool=pv.emit_section_tool or prompts.EMIT_SECTION_TOOL,
        )

    async def verify_section(
        self, section: dict[str, Any], payload: dict[str, Any], *, k: int
    ) -> dict[str, Any]:
        pv = prompts.get_prompt_version(payload.get("prompt_version"))
        return await self._call_tool(
            system_prompt=pv.system_verify,
            user_message=pv.render_verify(section, payload, k),
            tool=pv.verify_section_tool or prompts.VERIFY_SECTION_TOOL,
            # Spec mode verify is a blind solve — run it cool (§9.4). The v2
            # rewrite verify (spec_mode=False) keeps the default sampling.
            temperature=prompts.VERIFY_TEMPERATURE if pv.spec_mode else None,
        )

    async def analyze_section(self, payload: dict[str, Any]) -> dict[str, Any]:
        pv = prompts.get_prompt_version(payload.get("prompt_version"))
        if not pv.system_analyze or not pv.render_analyze:
            raise RuntimeError(f"prompt version {pv.name!r} has no analyze step")
        return await self._call_tool(
            system_prompt=pv.system_analyze,
            user_message=pv.render_analyze(payload),
            tool=prompts.EMIT_SKILL_MAP_TOOL,
            temperature=prompts.ANALYZE_TEMPERATURE,
        )

    async def fix_section(
        self, section: dict[str, Any], payload: dict[str, Any], *, k: int
    ) -> dict[str, Any]:
        pv = prompts.get_prompt_version(payload.get("prompt_version"))
        if not pv.system_fix or not pv.render_fix:
            raise RuntimeError(f"prompt version {pv.name!r} has no fix step")
        return await self._call_tool(
            system_prompt=pv.system_fix,
            user_message=pv.render_fix(section, payload, k),
            tool=pv.fix_section_tool or prompts.EMIT_SECTION_SPEC_TOOL,
            temperature=prompts.VERIFY_TEMPERATURE,
        )

    async def _call_tool(
        self, *, system_prompt: str, user_message: str, tool: dict[str, Any],
        temperature: float | None = None,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = dict(self._extra_create)
        if temperature is not None:  # analyze (0.2) + spec verify/fix (0.3) set this; generate + v2 verify leave it unset
            extra["temperature"] = temperature
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[_as_openai_tool(tool)],
            tool_choice={"type": "function", "function": {"name": tool["name"]}},
            **extra,
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
