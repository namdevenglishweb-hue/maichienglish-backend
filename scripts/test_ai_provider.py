"""Connectivity + generation smoke test for the ACTIVE text provider (REAL API).

Tests whatever AI_PROVIDER points at (openrouter | groq | anthropic). Usage:
    python scripts/test_ai_provider.py
    # or force one for a run:
    AI_PROVIDER=groq AI_MODEL=llama-3.3-70b-versatile python scripts/test_ai_provider.py

Step 1 — minimal chat (OpenAI-compatible providers): proves reachability + auth.
Step 2 — real emit_section tool-call via the factory: proves the generation path
         returns a well-formed section (question_data wrapper) + shows usage.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings

# (api_key, base_url) per OpenAI-compatible provider — for the step-1 probe only.
_OPENAI_COMPAT = {
    "openrouter": lambda s: (s.openrouter_api_key, s.openrouter_base_url),
    "groq": lambda s: (s.groq_api_key, s.groq_base_url),
}


async def main() -> None:
    s = get_settings()
    provider = s.ai_provider
    print(f"AI_PROVIDER={provider} | AI_MODEL={s.ai_model} | AI_MAX_TOKENS={s.ai_max_tokens}")

    # --- Step 1: minimal connectivity (OpenAI-compatible providers only) --
    if provider in _OPENAI_COMPAT:
        api_key, base_url = _OPENAI_COMPAT[provider](s)
        if not api_key:
            print(f"No API key for provider {provider!r} — set it in .env.")
            return
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        print(f"\n[1] Connect {base_url}")
        r = await client.chat.completions.create(
            model=s.ai_model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=20,
        )
        print("    content      :", repr(r.choices[0].message.content))
        print("    finish_reason:", r.choices[0].finish_reason)
        print("    usage        :", r.usage)
    else:
        print(f"\n[1] (skipped minimal probe for non-OpenAI-compatible provider {provider!r})")

    # --- Step 2: real tool-calling generate via the factory ---------------
    print(f"\n[2] emit_section via get_ai_generator()")
    from services.ai.generator import get_ai_generator
    from services.ai import prompts

    gen = get_ai_generator()
    tiny_section = {
        "type": "multiple_choice",
        "part_label": "Part 1",
        "instructions": "Read the text and answer.",
        "materials": [
            {"type": "text", "content": "Tom likes tea. He drinks it every morning before work."}
        ],
        "questions": [
            {
                "question_type": "multiple_choice",
                "question_data": {
                    "stem": "What does Tom like?",
                    "options": [{"text": "tea"}, {"text": "coffee"}, {"text": "juice"}],
                    "correct_index": 0,
                },
            }
        ],
    }
    payload = prompts.build_section_payload(
        tiny_section, {"level": "KET", "skill": "reading", "title": "Test"},
        section_prompt="Make it about a girl named Mai who likes coffee.",
    )
    try:
        out = await gen.generate_section(payload, k=1)
    except Exception as e:  # noqa: BLE001
        print("    FAILED:", type(e).__name__, "-", e)
        print("    usage:", getattr(gen, "usage", None))
        return

    q0 = (out.get("questions") or [{}])[0]
    print("    returned keys       :", list(out.keys()))
    print("    #questions           :", len(out.get("questions") or []))
    print("    q0 has question_data :", isinstance(q0.get("question_data"), dict))
    print("    usage                :", gen.usage)
    print("    --- section JSON (truncated) ---")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:1800])


if __name__ == "__main__":
    asyncio.run(main())
