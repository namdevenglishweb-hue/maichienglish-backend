"""Section CRUD — middle layer of Exam → Section → Question.

Owns passage materials (JSONB), optional listening audio + replay cap, and
the instruction rubric. Per-section position is the `N` referenced by
`{{gap:N}}` markers inside materials content (see plan §3.5).
"""
import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Materials validator — list of {type, label?, content} entries.
# Only the "text" variant is supported today; audio lives in section columns.
# ---------------------------------------------------------------------------


class _TextMaterial(BaseModel):
    type: Literal["text"]
    label: Optional[str] = None
    content: str = Field(..., min_length=1)


def _validate_materials(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError("materials must be a list")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("type") != "text":
            raise ValidationError(
                f"materials[{i}]: only {{type:'text'}} entries are supported"
            )
        try:
            out.append(_TextMaterial(**item).model_dump(exclude_none=True))
        except PydanticValidationError as e:
            raise ValidationError(f"materials[{i}]: {e.errors()}")
    return out


def _coerce_jsonb(raw):
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _row_to_section(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "exam_id": str(row["exam_id"]),
        "position": row["position"],
        "part_label": row["part_label"],
        "instructions": row["instructions"],
        "materials": _coerce_jsonb(row["materials"]) or [],
        "audio_url": row["audio_url"],
        "max_audio_plays": row["max_audio_plays"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
    }


_SELECT_COLS = """
    id, exam_id, position, part_label, instructions, materials,
    audio_url, max_audio_plays, created_at, updated_at, deleted_at
"""


class SectionService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_section(
        self,
        exam_id: str,
        part_label: Optional[str] = None,
        instructions: Optional[str] = None,
        materials: Optional[list[dict]] = None,
        audio_url: Optional[str] = None,
        max_audio_plays: Optional[int] = None,
        position: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create a new section under an exam.

        Args:
            exam_id: parent exam (must exist and not be soft-deleted).
            part_label: display label, e.g. "Part 1".
            instructions: rubric shown to the student.
            materials: list of `{type, label?, content}` passage entries.
                Validated server-side (only `text` variant is supported today).
            audio_url: listening sections only.
            max_audio_plays: listening sections only.
            position: 1-based order in the exam. If None, append after MAX.

        Returns:
            Created section dict (camel-free snake_case).

        Raises:
            NotFoundError: parent exam doesn't exist.
            ValidationError: materials shape is invalid.
        """
        validated_materials = _validate_materials(materials)

        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    "SELECT id FROM public.exams WHERE id = $1 AND deleted_at IS NULL",
                    exam_id,
                )
                if not exam:
                    raise NotFoundError(f"Exam {exam_id} not found")

                if position is None:
                    max_pos = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(position), 0)
                        FROM public.sections
                        WHERE exam_id = $1 AND deleted_at IS NULL
                        """,
                        exam_id,
                    )
                    position = max_pos + 1

                row = await conn.fetchrow(
                    f"""
                    INSERT INTO public.sections
                        (exam_id, position, part_label, instructions, materials,
                         audio_url, max_audio_plays)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                    RETURNING {_SELECT_COLS}
                    """,
                    exam_id,
                    position,
                    part_label,
                    instructions,
                    json.dumps(validated_materials),
                    audio_url,
                    max_audio_plays,
                )

        logger.info(
            "Created section %s (exam=%s, position=%d)",
            row["id"], exam_id, position,
        )
        return _row_to_section(row)

    async def list_sections_by_exam(
        self, exam_id: str
    ) -> list[dict[str, Any]]:
        """Return active (non-deleted) sections of an exam ordered by position."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.sections
                WHERE exam_id = $1 AND deleted_at IS NULL
                ORDER BY position ASC, created_at ASC
                """,
                exam_id,
            )
        return [_row_to_section(r) for r in rows]

    async def get_section(self, section_id: str) -> Optional[dict[str, Any]]:
        """Fetch a section by id. Returns None if it doesn't exist or is soft-deleted."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.sections
                WHERE id = $1 AND deleted_at IS NULL
                """,
                section_id,
            )
        return _row_to_section(row) if row else None

    async def update_section(self, section_id: str, **fields) -> dict[str, Any]:
        """Patch a section. Only whitelisted columns are accepted.

        Args:
            section_id: target row.
            **fields: subset of `part_label`, `instructions`, `materials`,
                `audio_url`, `max_audio_plays`, `position`.

        Raises:
            NotFoundError: section doesn't exist or is soft-deleted.
            ValidationError: no updatable fields supplied, or materials invalid.
        """
        if not fields:
            raise ValidationError("No fields to update")

        allowed = {
            "part_label",
            "instructions",
            "materials",
            "audio_url",
            "max_audio_plays",
            "position",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValidationError("No updatable fields supplied")

        if "materials" in updates:
            updates["materials"] = _validate_materials(updates["materials"])

        set_parts = []
        params: list[Any] = []
        for k, v in updates.items():
            params.append(json.dumps(v) if k == "materials" else v)
            cast = "::jsonb" if k == "materials" else ""
            set_parts.append(f"{k} = ${len(params)}{cast}")
        params.append(section_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.sections
                SET {', '.join(set_parts)}, updated_at = now()
                WHERE id = ${len(params)} AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                *params,
            )
        if not row:
            raise NotFoundError(f"Section {section_id} not found")
        logger.info("Updated section %s (fields=%s)", section_id, list(updates))
        return _row_to_section(row)

    async def soft_delete_section(self, section_id: str) -> None:
        """Mark a section as deleted (sets `deleted_at = now()`). Questions
        remain in the DB but become unreachable through the published tree.

        Raises:
            NotFoundError: section doesn't exist or is already deleted.
        """
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE public.sections
                SET deleted_at = now(), updated_at = now()
                WHERE id = $1 AND deleted_at IS NULL
                """,
                section_id,
            )
        updated = int(result.split()[-1]) if result else 0
        if updated == 0:
            raise NotFoundError(f"Section {section_id} not found")
        logger.info("Soft-deleted section %s", section_id)

    async def hard_delete_section(self, section_id: str) -> None:
        """Hard-delete a section row. `CASCADE`s through questions, answers,
        and `attempt_section_state`. Use only for broken/erroneous content.

        Raises:
            NotFoundError: section doesn't exist.
        """
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM public.sections WHERE id = $1",
                section_id,
            )
        deleted = int(result.split()[-1]) if result else 0
        if deleted == 0:
            raise NotFoundError(f"Section {section_id} not found")
        logger.info("Hard-deleted section %s (CASCADE)", section_id)


section_service = SectionService()
