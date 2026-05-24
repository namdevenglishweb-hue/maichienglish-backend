import logging
from typing import Any, Optional

from services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


def _row_to_exam(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "level": row["level"],
        "skill": row["skill"],
        "duration_minutes": row["duration_minutes"],
        "description": row["description"],
        "is_published": row["is_published"],
        "created_by": str(row["created_by"]) if row["created_by"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
    }


_SELECT_COLS = """
    id, title, level, skill, duration_minutes, description,
    is_published, created_by, created_at, updated_at, deleted_at
"""


class ExamService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_exam(
        self,
        title: str,
        level: str,
        skill: str,
        duration_minutes: int = 45,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.exams
                    (title, level, skill, duration_minutes, description, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {_SELECT_COLS}
                """,
                title,
                level,
                skill,
                duration_minutes,
                description,
                created_by,
            )
        logger.info("Created exam: %s (level=%s, skill=%s)", row["id"], level, skill)
        return _row_to_exam(row)

    async def get_exam(self, exam_id: str) -> Optional[dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.exams
                WHERE id = $1 AND deleted_at IS NULL
                """,
                exam_id,
            )
        return _row_to_exam(row) if row else None

    async def list_exams(
        self,
        level: Optional[str] = None,
        skill: Optional[str] = None,
        is_published: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if level is not None:
            params.append(level)
            clauses.append(f"level = ${len(params)}")
        if skill is not None:
            params.append(skill)
            clauses.append(f"skill = ${len(params)}")
        if is_published is not None:
            params.append(is_published)
            clauses.append(f"is_published = ${len(params)}")

        where = " AND ".join(clauses)
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SELECT_COLS}
                FROM public.exams
                WHERE {where}
                ORDER BY created_at DESC
                """,
                *params,
            )
        return [_row_to_exam(r) for r in rows]

    async def update_exam(self, exam_id: str, **fields) -> dict[str, Any]:
        if not fields:
            raise ValidationError("No fields to update")

        allowed = {
            "title",
            "level",
            "skill",
            "duration_minutes",
            "description",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValidationError("No updatable fields supplied")

        set_parts = []
        params: list[Any] = []
        for k, v in updates.items():
            params.append(v)
            set_parts.append(f"{k} = ${len(params)}")
        params.append(exam_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.exams
                SET {', '.join(set_parts)}, updated_at = now()
                WHERE id = ${len(params)} AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                *params,
            )
        if not row:
            logger.warning("update_exam: exam %s not found or already deleted", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Updated exam %s (fields=%s)", exam_id, list(updates))
        return _row_to_exam(row)

    async def publish_exam(self, exam_id: str) -> dict[str, Any]:
        """Publish if the exam has at least one active question across all sections."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                exam = await conn.fetchrow(
                    "SELECT id FROM public.exams WHERE id = $1 AND deleted_at IS NULL",
                    exam_id,
                )
                if not exam:
                    logger.warning("publish_exam: exam %s not found", exam_id)
                    raise NotFoundError(f"Exam {exam_id} not found")

                count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM public.questions q
                    JOIN public.sections s ON s.id = q.section_id
                    WHERE s.exam_id = $1
                      AND s.deleted_at IS NULL
                      AND q.deleted_at IS NULL
                    """,
                    exam_id,
                )
                if count == 0:
                    logger.warning(
                        "publish_exam: exam %s has no active sections/questions", exam_id
                    )
                    raise ValidationError(
                        "Cannot publish exam with no active questions"
                    )

                row = await conn.fetchrow(
                    f"""
                    UPDATE public.exams
                    SET is_published = true, updated_at = now()
                    WHERE id = $1
                    RETURNING {_SELECT_COLS}
                    """,
                    exam_id,
                )
        logger.info("Published exam %s (%d active questions)", exam_id, count)
        return _row_to_exam(row)

    async def unpublish_exam(self, exam_id: str) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.exams
                SET is_published = false, updated_at = now()
                WHERE id = $1 AND deleted_at IS NULL
                RETURNING {_SELECT_COLS}
                """,
                exam_id,
            )
        if not row:
            logger.warning("unpublish_exam: exam %s not found", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Unpublished exam %s", exam_id)
        return _row_to_exam(row)

    async def soft_delete_exam(self, exam_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE public.exams
                SET deleted_at = now(), updated_at = now(), is_published = false
                WHERE id = $1 AND deleted_at IS NULL
                """,
                exam_id,
            )
        updated = int(result.split()[-1]) if result else 0
        if updated == 0:
            logger.warning("soft_delete_exam: exam %s not found or already deleted", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Soft-deleted exam %s", exam_id)

    async def hard_delete_exam(self, exam_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM public.exams WHERE id = $1",
                exam_id,
            )
        deleted = int(result.split()[-1]) if result else 0
        if deleted == 0:
            logger.warning("hard_delete_exam: exam %s not found", exam_id)
            raise NotFoundError(f"Exam {exam_id} not found")
        logger.info("Hard-deleted exam %s (CASCADE)", exam_id)


exam_service = ExamService()
