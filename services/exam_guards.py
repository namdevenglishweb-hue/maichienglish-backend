"""Publish-lock guards — block editing a published exam's CONTENT.

docs/exam-publish-lock/. Once an exam is `is_published=true`:
  - its content (sections / questions / materials) is frozen → 409;
  - metadata (PATCH exam) is still editable;
  - it can only be unpublished while it has NO attempts (otherwise frozen
    forever to protect attempt integrity: scores, highlight offsets, …).

All guards take an open connection so they run inside the caller's
transaction, *before* the write. They raise ConflictError (→ 409); the
resolver helpers resolve the owning exam_id from a section/question id and
are no-ops when the id doesn't resolve (caller's own not-found handling
then applies).
"""

from services.exceptions import ConflictError


async def assert_exam_content_editable(conn, exam_id) -> None:
    """409 if the exam is published (content is frozen)."""
    pub = await conn.fetchval(
        "SELECT is_published FROM public.exams "
        "WHERE id = $1 AND deleted_at IS NULL",
        exam_id,
    )
    if pub:  # None (not found) → let caller's not-found path handle it
        raise ConflictError(
            "Exam is published; unpublish it first to edit its content"
        )


async def assert_no_published_among(conn, exam_ids) -> None:
    """Batch variant: 409 if ANY exam in the set is published."""
    ids = [e for e in (exam_ids or []) if e is not None]
    if not ids:
        return
    pub = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.exams "
        "WHERE id = ANY($1::uuid[]) AND is_published)",
        ids,
    )
    if pub:
        raise ConflictError(
            "One or more target items belong to a published exam; "
            "unpublish it first to edit its content"
        )


async def assert_exam_has_no_attempts(conn, exam_id) -> None:
    """409 if the exam already has any attempt (cannot unpublish)."""
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.attempts WHERE exam_id = $1)",
        exam_id,
    )
    if exists:
        raise ConflictError(
            "Exam already has attempts; it can no longer be unpublished or edited"
        )


# --- resolver helpers (so each mutation calls exactly one guard) --------- #


async def assert_section_editable(conn, section_id) -> None:
    """Guard a single section by id (resolves its owning exam)."""
    exam_id = await conn.fetchval(
        "SELECT exam_id FROM public.sections WHERE id = $1", section_id
    )
    if exam_id is not None:
        await assert_exam_content_editable(conn, exam_id)


async def assert_question_editable(conn, question_id) -> None:
    """Guard a single question by id (question → section → exam)."""
    exam_id = await conn.fetchval(
        "SELECT s.exam_id FROM public.questions q "
        "JOIN public.sections s ON s.id = q.section_id WHERE q.id = $1",
        question_id,
    )
    if exam_id is not None:
        await assert_exam_content_editable(conn, exam_id)


async def assert_sections_editable(conn, section_ids) -> None:
    """Batch guard for a list of section ids."""
    rows = await conn.fetch(
        "SELECT DISTINCT exam_id FROM public.sections WHERE id = ANY($1::uuid[])",
        list(section_ids),
    )
    await assert_no_published_among(conn, [r["exam_id"] for r in rows])


async def assert_questions_editable(conn, question_ids) -> None:
    """Batch guard for a list of question ids."""
    rows = await conn.fetch(
        "SELECT DISTINCT s.exam_id FROM public.questions q "
        "JOIN public.sections s ON s.id = q.section_id WHERE q.id = ANY($1::uuid[])",
        list(question_ids),
    )
    await assert_no_published_among(conn, [r["exam_id"] for r in rows])
