"""Async job tracking for AI image generation (§7).

CRUD over `image_generation_jobs` (migration 0021) + `run_image_job`, the
coroutine driven by FastAPI BackgroundTasks. One job = one image. No cancel
(jobs are short). report is camelCase jsonb.
"""

import json
import logging
from typing import Any, Optional

from services.exceptions import NotFoundError
from services.image_generation_service import (
    ImageGenerationError,
    generate_one_image,
)

logger = logging.getLogger(__name__)


def _coerce(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def job_to_view(r) -> dict[str, Any]:
    return {
        "jobId": str(r["id"]),
        "status": r["status"],
        "mode": r["mode"],
        "description": r["description"],
        "sourceImageUrl": r["source_image_url"],
        "resultUrl": r["result_url"],
        "report": _coerce(r["report"]),
        "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
        "updatedAt": r["updated_at"].isoformat() if r["updated_at"] else None,
        "finishedAt": r["finished_at"].isoformat() if r["finished_at"] else None,
    }


class ImageJobService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_job(
        self, *, description: str, source_image_url: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict[str, Any]:
        mode = "edit" if source_image_url else "generate"
        async with self.db.acquire() as conn:
            r = await conn.fetchrow(
                """
                INSERT INTO public.image_generation_jobs
                    (description, source_image_url, mode, created_by)
                VALUES ($1, $2, $3, $4)
                RETURNING *
                """,
                description, source_image_url, mode, created_by,
            )
        return job_to_view(r)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT * FROM public.image_generation_jobs WHERE id = $1", job_id
            )
        if not r:
            raise NotFoundError(f"Image job {job_id} not found")
        return job_to_view(r)

    async def list_jobs(
        self, *, status: Optional[str] = None, limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM public.image_generation_jobs {where} "
                f"ORDER BY created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
                *params,
            )
        return [job_to_view(r) for r in rows]

    async def mark_running(self, job_id: str) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE public.image_generation_jobs SET status='running', "
                "updated_at=now() WHERE id=$1", job_id,
            )

    async def finish(
        self, job_id: str, status: str, *, result_url=None, report=None,
    ) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE public.image_generation_jobs SET status=$2, result_url=$3, "
                "report=$4::jsonb, finished_at=now(), updated_at=now() WHERE id=$1",
                job_id, status, result_url,
                json.dumps(report) if report is not None else None,
            )


image_job_service = ImageJobService()


async def run_image_job(
    *, job_id: str, description: str,
    source_image_url: Optional[str] = None, exam_context: Optional[dict] = None,
) -> None:
    """Drive one image job to a terminal state. Swallows errors into status."""
    svc = image_job_service
    try:
        await svc.mark_running(job_id)
        result = await generate_one_image(
            description, source_image_url=source_image_url, exam_context=exam_context,
        )
        await svc.finish(
            job_id, "succeeded", result_url=result["image_url"],
            report={"rounds": result["rounds"], "usage": result["usage"]},
        )
    except ImageGenerationError as e:
        await svc.finish(job_id, "failed", report={"verifyReason": e.reason})
    except Exception as e:  # noqa: BLE001 — last-resort, must not crash the worker
        logger.exception("Image job %s failed", job_id)
        await svc.finish(job_id, "failed", report={"verifyReason": str(e)})
