"""Admin per-type prompt config for AI exam generation (source A).

One row per section type in `section_type_prompts` (migration 0019). The
generation service reads `load_map()` once per run and injects the matching
prompt for each section's type. CRUD is admin-only (api layer). See
docs/exam-ai-generation/exam-ai-generation-design.md §10.
"""

import logging
from typing import Any, Optional

from services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Mirrors the CHECK on section_type_prompts.type (and sections.type).
ALLOWED_TYPES: frozenset[str] = frozenset({
    "multiple_choice", "multiple_choice_shared", "fill_blank",
    "matching", "writing", "speaking", "form_completion",
})


def _row(r) -> dict[str, Any]:
    return {
        "type": r["type"],
        "additional_prompt": r["additional_prompt"],
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        "updated_by": str(r["updated_by"]) if r["updated_by"] else None,
    }


class SectionTypePromptService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def list_prompts(self) -> list[dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT type, additional_prompt, updated_at, updated_by "
                "FROM public.section_type_prompts ORDER BY type"
            )
        return [_row(r) for r in rows]

    async def load_map(self) -> dict[str, str]:
        """`{type: additional_prompt}` — consumed by the generation service."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT type, additional_prompt FROM public.section_type_prompts"
            )
        return {r["type"]: r["additional_prompt"] for r in rows}

    async def upsert(
        self, type_: str, additional_prompt: str, updated_by: Optional[str] = None
    ) -> dict[str, Any]:
        if type_ not in ALLOWED_TYPES:
            raise ValidationError(
                f"invalid section type {type_!r}; allowed: {sorted(ALLOWED_TYPES)}"
            )
        if not additional_prompt or not additional_prompt.strip():
            raise ValidationError("additional_prompt must be non-empty")
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.section_type_prompts
                    (type, additional_prompt, updated_at, updated_by)
                VALUES ($1, $2, now(), $3)
                ON CONFLICT (type) DO UPDATE
                    SET additional_prompt = EXCLUDED.additional_prompt,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                RETURNING type, additional_prompt, updated_at, updated_by
                """,
                type_, additional_prompt, updated_by,
            )
        logger.info("Upserted section_type_prompt for %s", type_)
        return _row(row)

    async def delete(self, type_: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM public.section_type_prompts WHERE type = $1", type_
            )
        if result and int(result.split()[-1]) == 0:
            raise NotFoundError(f"No prompt configured for type {type_!r}")


section_type_prompt_service = SectionTypePromptService()
