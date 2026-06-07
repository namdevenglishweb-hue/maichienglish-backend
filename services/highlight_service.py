"""Attempt highlights — student highlight + optional note while taking an attempt.

Owns CRUD for `attempt_highlights` (docs/attempt-highlights/):
  - Mutation (create/update/delete): owner of the attempt + attempt
    in_progress. Otherwise NotFoundError / PermissionDeniedError /
    ValidationError → routes map to 404 / 403 / 400.
  - `list_for_attempt(conn, attempt_id)` powers the READ path, which is
    *embedded* into the resume + detail payloads (no GET list endpoint).

`target_key` is opaque to the backend — we store/return it verbatim and
never parse it (FE↔BE convention). Offsets are stored as-is (the BE never
slices the source text), so JS UTF-16 vs Python codepoint offsets are a
non-issue here.

No HTTP imports — routes translate ServiceError subclasses to status codes.
"""

import logging
from typing import Any, Optional

import asyncpg

from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

logger = logging.getLogger(__name__)


def _row_to_highlight(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "targetKey": row["target_key"],
        "rangeStart": row["range_start"],
        "rangeEnd": row["range_end"],
        "quotedText": row["quoted_text"],
        "note": row["note"],
        "color": row["color"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
    }


class HighlightService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    # ------------------------------------------------------------------ #
    # Internal — fetch the attempt and assert owner + in_progress         #
    # ------------------------------------------------------------------ #

    async def _require_owned_in_progress(self, conn, attempt_id: str, user_id: str):
        """Raise NotFoundError (missing) / PermissionDeniedError (not owner)
        / ValidationError (not in_progress). Returns the attempt row."""
        try:
            attempt = await conn.fetchrow(
                "SELECT user_id, submitted_at, is_abandoned "
                "FROM public.attempts WHERE id = $1",
                attempt_id,
            )
        except asyncpg.DataError:
            attempt = None
        if not attempt:
            raise NotFoundError(f"Attempt {attempt_id} not found")
        if str(attempt["user_id"]) != str(user_id):
            raise PermissionDeniedError("Not the owner of this attempt")
        if attempt["submitted_at"] is not None or attempt["is_abandoned"]:
            raise ValidationError("Attempt is not in progress")
        return attempt

    # ------------------------------------------------------------------ #
    # Mutations (owner + in_progress)                                     #
    # ------------------------------------------------------------------ #

    async def create_highlight(
        self,
        attempt_id: str,
        user_id: str,
        *,
        target_key: str,
        range_start: int,
        range_end: int,
        quoted_text: str,
        note: Optional[str] = None,
        color: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            await self._require_owned_in_progress(conn, attempt_id, user_id)
            row = await conn.fetchrow(
                """
                INSERT INTO public.attempt_highlights
                    (attempt_id, target_key, range_start, range_end,
                     quoted_text, note, color)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id, target_key, range_start, range_end,
                          quoted_text, note, color, created_at
                """,
                attempt_id, target_key, range_start, range_end,
                quoted_text, note, color,
            )
        logger.info(
            "Created highlight %s on attempt %s (targetKey=%s)",
            row["id"], attempt_id, target_key,
        )
        return _row_to_highlight(row)

    async def update_highlight(
        self,
        attempt_id: str,
        highlight_id: str,
        user_id: str,
        *,
        note: Optional[str] = None,
        color: Optional[str] = None,
        note_set: bool = False,
        color_set: bool = False,
    ) -> dict[str, Any]:
        """Patch note and/or color. 404 if the highlight isn't under an
        attempt owned by `user_id` (don't leak existence); 400 if the
        attempt isn't in_progress."""
        async with self.db.acquire() as conn:
            owner_row = await self._fetch_highlight_owner(
                conn, attempt_id, highlight_id
            )
            if not owner_row or str(owner_row["user_id"]) != str(user_id):
                raise NotFoundError(f"Highlight {highlight_id} not found")
            if owner_row["submitted_at"] is not None or owner_row["is_abandoned"]:
                raise ValidationError("Attempt is not in progress")

            sets = ["updated_at = now()"]
            vals: list[Any] = []
            if note_set:
                vals.append(note)
                sets.append(f"note = ${len(vals)}")
            if color_set:
                vals.append(color)
                sets.append(f"color = ${len(vals)}")
            vals.append(highlight_id)
            row = await conn.fetchrow(
                "UPDATE public.attempt_highlights SET "
                + ", ".join(sets)
                + f" WHERE id = ${len(vals)} "
                + "RETURNING id, target_key, range_start, range_end, "
                + "quoted_text, note, color, created_at",
                *vals,
            )
        return _row_to_highlight(row)

    async def delete_highlight(
        self, attempt_id: str, highlight_id: str, user_id: str
    ) -> None:
        async with self.db.acquire() as conn:
            owner_row = await self._fetch_highlight_owner(
                conn, attempt_id, highlight_id
            )
            if not owner_row or str(owner_row["user_id"]) != str(user_id):
                raise NotFoundError(f"Highlight {highlight_id} not found")
            if owner_row["submitted_at"] is not None or owner_row["is_abandoned"]:
                raise ValidationError("Attempt is not in progress")
            await conn.execute(
                "DELETE FROM public.attempt_highlights WHERE id = $1",
                highlight_id,
            )

    async def _fetch_highlight_owner(self, conn, attempt_id: str, highlight_id: str):
        """Highlight joined with its attempt's owner + state, scoped to
        attempt_id. None if not found / malformed id."""
        try:
            return await conn.fetchrow(
                """
                SELECT a.user_id, a.submitted_at, a.is_abandoned
                FROM public.attempt_highlights h
                JOIN public.attempts a ON a.id = h.attempt_id
                WHERE h.id = $1 AND h.attempt_id = $2
                """,
                highlight_id, attempt_id,
            )
        except asyncpg.DataError:
            return None

    # ------------------------------------------------------------------ #
    # Read — embed (no standalone GET list endpoint)                      #
    # ------------------------------------------------------------------ #

    async def list_for_attempt(self, conn, attempt_id: str) -> list[dict[str, Any]]:
        """All highlights of an attempt (oldest first). Takes an open
        connection so it composes inside the resume/detail builders."""
        rows = await conn.fetch(
            """
            SELECT id, target_key, range_start, range_end,
                   quoted_text, note, color, created_at
            FROM public.attempt_highlights
            WHERE attempt_id = $1
            ORDER BY created_at ASC
            """,
            attempt_id,
        )
        return [_row_to_highlight(r) for r in rows]


highlight_service = HighlightService()
