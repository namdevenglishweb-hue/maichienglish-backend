"""Runtime-editable AI generation settings (singleton row, migration 0022).

Lets an admin change the AI generation defaults (provider / model / max_tokens /
self_review_rounds) without an env change + redeploy. Each stored column is
NULLABLE — NULL means "use the env default". Resolution at generation time:
**per-request override > this row > env**.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Columns an admin may set (others are server-managed).
_FIELDS = ("provider", "model", "max_tokens", "self_review_rounds")


class AISettingsService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def get_stored(self) -> Optional[dict[str, Any]]:
        """The raw singleton row (or None if never set / table not migrated yet).

        Tolerates a missing table so generation keeps working on env defaults if
        this is deployed before migration 0022 is applied.
        """
        import asyncpg
        try:
            async with self.db.acquire() as conn:
                r = await conn.fetchrow(
                    "SELECT * FROM public.ai_generation_settings WHERE id = 1"
                )
            return dict(r) if r else None
        except asyncpg.UndefinedTableError:
            logger.warning("ai_generation_settings not migrated yet; using env defaults")
            return None

    async def get_effective(self) -> dict[str, Any]:
        """Resolve each field: stored value if set, else the env default."""
        row = await self.get_stored()
        from config.settings import get_settings
        s = get_settings()

        def pick(col, default):
            v = row.get(col) if row else None
            return v if v is not None else default

        return {
            "provider": pick("provider", s.ai_provider),
            "model": pick("model", s.ai_model),
            "max_tokens": pick("max_tokens", s.ai_max_tokens),
            "self_review_rounds": pick("self_review_rounds", s.ai_self_review_rounds),
        }

    async def update(
        self, fields: dict[str, Any], *, updated_by: Optional[str] = None
    ) -> dict[str, Any]:
        """Upsert the singleton, setting only the provided columns (others kept).

        A field explicitly set to None clears it (→ falls back to env default).
        """
        cols = [c for c in _FIELDS if c in fields]
        # Build: INSERT (id, <cols>, updated_by) VALUES (1, ...) ON CONFLICT (id)
        #        DO UPDATE SET <cols>=EXCLUDED..., updated_by=..., updated_at=now()
        set_cols = cols + ["updated_by"]
        placeholders = ", ".join(f"${i + 2}" for i in range(len(set_cols)))
        col_list = ", ".join(set_cols)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in set_cols)
        params = [fields[c] for c in cols] + [updated_by]
        async with self.db.acquire() as conn:
            await conn.execute(
                f"INSERT INTO public.ai_generation_settings (id, {col_list}) "
                f"VALUES (1, {placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = now()",
                *params,
            )
        return await self.get_effective()


ai_settings_service = AISettingsService()
