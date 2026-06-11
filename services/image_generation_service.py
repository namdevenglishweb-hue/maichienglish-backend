"""AI image generation — the reusable per-image core.

`generate_one_image` mirrors `generate_one_section`: edit-or-generate →
vision-verify (Tầng A) → upload, with a retry budget. On failure it raises
`ImageGenerationError` so the caller (run_image_job) marks the job failed and
the FE keeps `pendingReplacement=true` (manual). FE drives the batch (N images
= N jobs). See docs/exam-image-generation §3.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ImageGenerationError(Exception):
    """An image could not be produced/verified within its budget (§3)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _resolve_rounds(rounds: Optional[int]) -> int:
    if rounds is not None:
        return rounds
    from config.settings import get_settings
    return get_settings().image_verify_rounds


async def generate_one_image(
    description: str,
    *,
    source_image_url: Optional[str] = None,
    exam_context: Optional[dict] = None,
    generator=None,
    storage=None,
    rounds: Optional[int] = None,
) -> dict[str, Any]:
    """Produce one image and return `{image_url, mode, rounds, usage}`.

    `source_image_url` present ⇒ edit mode (giữ layout/chữ), else generate.
    `IMAGE_VERIFY_ROUNDS=0` ⇒ skip verify (trust first image). Raises
    `ImageGenerationError` when no acceptable image after the budget.
    """
    if not description or not description.strip():
        raise ImageGenerationError("empty description")

    from services.ai.image_generator import get_image_generator
    from services.storage_service import get_storage_service

    gen = generator or get_image_generator()
    store = storage or get_storage_service()
    verify_rounds = _resolve_rounds(rounds)
    mode = "edit" if source_image_url else "generate"
    attempts = max(1, verify_rounds)
    last_reason = "unknown"

    for attempt in range(1, attempts + 1):
        # Feed the reviewer's rejection back into the retry — regenerating
        # with the identical prompt mostly reproduces the identical failure
        # (mirrors exam-gen's retry_error mechanism).
        desc = description if attempt == 1 else (
            f"{description}\n\nYOUR PREVIOUS IMAGE WAS REJECTED by the reviewer "
            f"for this reason: {last_reason}. Generate a new image that fixes "
            "exactly that problem."
        )
        if mode == "edit":
            img, mime = await gen.edit_image(
                source_image_url, desc, exam_context=exam_context
            )
        else:
            img, mime = await gen.generate_image(desc, exam_context=exam_context)

        if verify_rounds == 0:
            url = await store.upload_bytes("images", mime, img)
            return {"image_url": url, "mode": mode, "rounds": 0,
                    "usage": getattr(gen, "usage", {})}

        verdict = await gen.verify_image(img, mime, description)
        if verdict.get("is_acceptable"):
            url = await store.upload_bytes("images", mime, img)
            return {"image_url": url, "mode": mode, "rounds": attempt,
                    "usage": getattr(gen, "usage", {})}
        last_reason = verdict.get("reason") or "image did not match the description"

    raise ImageGenerationError(last_reason)
