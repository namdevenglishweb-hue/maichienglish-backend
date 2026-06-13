"""Async job tracking for AI exam generation (Phase 2, §14).

CRUD over `exam_generation_jobs` (migration 0020) plus `run_generation_job`,
the coroutine driven by FastAPI BackgroundTasks. Job rows let the FE poll
progress while generation runs out-of-band. See design §14.1–14.3.
"""

import json
import logging
from typing import Any, Optional

from services.exceptions import NotFoundError
from services.exam_generation_service import (
    GenerationAborted,
    exam_generation_service,
)

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised by the progress callback when an admin requested cancel (§14.3)."""


def job_to_view(r) -> dict[str, Any]:
    """Row → camelCase API view."""
    return {
        "jobId": str(r["id"]),
        "scope": r["scope"],
        "status": r["status"],
        "sourceExamId": str(r["source_exam_id"]),
        "targetSectionId": str(r["target_section_id"]) if r["target_section_id"] else None,
        "k": r["k"],
        "title": r["title"],
        "sectionsTotal": r["sections_total"],
        "sectionsDone": r["sections_done"],
        "currentSection": r["current_section"],
        "resultExamId": str(r["result_exam_id"]) if r["result_exam_id"] else None,
        "report": _coerce(r["report"]),
        "abortedReason": r["aborted_reason"],
        "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
        "updatedAt": r["updated_at"].isoformat() if r["updated_at"] else None,
        "finishedAt": r["finished_at"].isoformat() if r["finished_at"] else None,
    }


def _coerce(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


_TERMINAL = ("succeeded", "failed", "aborted")


class GenerationJobService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    async def create_job(
        self, *, scope: str, source_exam_id: str, k: int,
        title: Optional[str] = None, target_section_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            r = await conn.fetchrow(
                """
                INSERT INTO public.exam_generation_jobs
                    (scope, source_exam_id, target_section_id, k, title, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                scope, source_exam_id, target_section_id, k, title, created_by,
            )
        return job_to_view(r)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT * FROM public.exam_generation_jobs WHERE id = $1", job_id
            )
        if not r:
            raise NotFoundError(f"Job {job_id} not found")
        return job_to_view(r)

    async def list_jobs(
        self, *, status: Optional[str] = None, scope: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if scope:
            params.append(scope)
            clauses.append(f"scope = ${len(params)}")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM public.exam_generation_jobs {where} "
                f"ORDER BY created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
                *params,
            )
        return [job_to_view(r) for r in rows]

    async def request_cancel(self, job_id: str) -> dict[str, Any]:
        """Set cancel_requested. No-op on terminal jobs. 404 if missing."""
        async with self.db.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT status FROM public.exam_generation_jobs WHERE id = $1", job_id
            )
            if not r:
                raise NotFoundError(f"Job {job_id} not found")
            if r["status"] not in _TERMINAL:
                await conn.execute(
                    "UPDATE public.exam_generation_jobs "
                    "SET cancel_requested = true, updated_at = now() WHERE id = $1",
                    job_id,
                )
        return await self.get_job(job_id)

    async def is_cancel_requested(self, conn, job_id: str) -> bool:
        return bool(await conn.fetchval(
            "SELECT cancel_requested FROM public.exam_generation_jobs WHERE id = $1",
            job_id,
        ))

    async def mark_running(self, job_id: str, sections_total: Optional[int]) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE public.exam_generation_jobs SET status='running', "
                "sections_total=$2, updated_at=now() WHERE id=$1",
                job_id, sections_total,
            )

    async def update_progress(self, job_id: str, done: int, current: int) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE public.exam_generation_jobs SET sections_done=$2, "
                "current_section=$3, updated_at=now() WHERE id=$1",
                job_id, done, current,
            )

    async def finish(
        self, job_id: str, status: str, *, report=None,
        result_exam_id=None, aborted_reason=None,
    ) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE public.exam_generation_jobs SET status=$2, report=$3::jsonb, "
                "result_exam_id=$4, aborted_reason=$5, "
                # On success show the bar full (progress_cb stops one short).
                "sections_done=CASE WHEN $2='succeeded' THEN coalesce(sections_total, sections_done) "
                "ELSE sections_done END, "
                "current_section=NULL, finished_at=now(), updated_at=now() WHERE id=$1",
                job_id, status,
                json.dumps(report) if report is not None else None,
                result_exam_id, aborted_reason,
            )


generation_job_service = GenerationJobService()


# ---------------------------------------------------------------------------
# Background runner — wires job state to the generation engine.
# ---------------------------------------------------------------------------


async def run_generation_job(
    *, job_id: str, scope: str, source_exam_id: str, k: int,
    title: Optional[str] = None, target_section_id: Optional[str] = None,
    created_by: Optional[str] = None,
    section_prompts: Optional[dict[str, str]] = None,
    model: Optional[str] = None, provider: Optional[str] = None,
    prompt_version: Optional[str] = None,
    part_code: Optional[str] = None,
) -> None:
    """Drive one job to a terminal state. Swallows all errors into job status."""
    svc = generation_job_service

    async def progress_cb(done: int, total: int) -> None:
        async with svc.db.acquire() as conn:
            if await svc.is_cancel_requested(conn, job_id):
                raise JobCancelled()
        await svc.update_progress(job_id, done, done + 1)

    try:
        # total is computed inside the engine loaders; for the job row we set it
        # via mark_running using a cheap pre-count (section scope = 1).
        await svc.mark_running(job_id, await _precount(scope, source_exam_id))
        if scope == "exam":
            report = await exam_generation_service.generate_similar_exam(
                source_exam_id, k, created_by=created_by, title=title,
                section_prompts=section_prompts, progress_cb=progress_cb,
                model=model, provider=provider, prompt_version=prompt_version,
            )
            await svc.finish(job_id, "succeeded", report=report,
                              result_exam_id=report.get("new_exam_id"))
        elif scope == "exam_preview":
            report = await exam_generation_service.generate_sections_preview(
                source_exam_id, k, section_prompts=section_prompts,
                progress_cb=progress_cb, model=model, provider=provider,
                prompt_version=prompt_version,
            )
            await svc.finish(job_id, "succeeded", report=report)
        elif scope == "section":
            report = await exam_generation_service.generate_one_part(
                target_section_id, k,
                section_prompt=(section_prompts or {}).get(str(target_section_id)),
                model=model, provider=provider, prompt_version=prompt_version,
                part_code=part_code,
            )
            await svc.finish(job_id, "succeeded", report=report)
        else:
            raise ValueError(f"unknown scope {scope!r}")
    except JobCancelled:
        await svc.finish(job_id, "aborted", aborted_reason="cancelled by admin")
    except GenerationAborted as e:
        await svc.finish(job_id, "failed", report=e.report, aborted_reason=e.reason)
    except Exception as e:  # noqa: BLE001 — last-resort, must not crash the worker
        logger.exception("Generation job %s failed", job_id)
        await svc.finish(job_id, "failed", aborted_reason=str(e))


async def _precount(scope: str, source_exam_id: str) -> Optional[int]:
    if scope == "section":
        return 1
    async with generation_job_service.db.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM public.sections WHERE exam_id = $1 "
            "AND deleted_at IS NULL", source_exam_id,
        )
