"""Curated AI model catalog for the FE generation dropdown.

ONE place to edit the recommended list — no ids scattered through routes/FE.
Distinct from GET /models (dynamic passthrough of EVERYTHING the provider
offers): this is the short, hand-picked list of combos verified to work with
the exam-generation pipeline (forced tool-calling), with human labels.

Curation notes (2026-06):
  - anthropic/claude-sonnet-4.5 + openai/gpt-oss-120b verified live via
    scripts/ab_matrix.py.
  - google/gemini-2.5-flash is EXCLUDED on purpose — it fails forced
    function calls (MALFORMED_FUNCTION_CALL / finish_reason=error).
  - groq free tier has a small daily token quota — fine for single parts.
  - provider 'anthropic' (direct SDK) is excluded while ANTHROPIC_API_KEY
    is not provisioned; add an entry here once it is.
"""

from typing import Any

# Each entry: provider (must be in generator.KNOWN_PROVIDERS), model id/slug
# for that provider, short human label, optional note shown in the picker.
CURATED_MODELS: list[dict[str, str]] = [
    {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.5",
        "label": "Claude Sonnet 4.5",
        "note": "Chất lượng cao nhất, đắt + chậm hơn.",
    },
    {
        "provider": "openrouter",
        "model": "openai/gpt-oss-120b",
        "label": "GPT-OSS 120B",
        "note": "Rẻ, nhanh, đã kiểm chứng với pipeline.",
    },
    {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "label": "Llama 3.3 70B (Groq)",
        "note": "Miễn phí nhưng quota/ngày nhỏ; hợp gen lẻ từng part.",
    },
    {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "label": "Gemini 3 Flash (direct)",
        "note": "Nhanh; self-review nên để 0 vòng nếu verify chập chờn.",
    },
]


async def get_model_catalog() -> dict[str, Any]:
    """Catalog + the currently-effective default (per-request > DB > env)."""
    from services.ai_settings_service import ai_settings_service

    eff = await ai_settings_service.get_effective()
    return {
        "default": {"provider": eff["provider"], "model": eff["model"]},
        "models": CURATED_MODELS,
    }
