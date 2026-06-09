"""Content-edit guards — freeze an exam's CONTENT once it has attempts.

docs/exam-publish-lock/. The thing worth protecting is attempt integrity
(scores, highlight offsets, …), so an exam's content (sections / questions /
materials) is frozen → 409 once it has ANY attempt — regardless of publish
state. Metadata (PATCH exam) stays editable, and an exam can ALWAYS be
unpublished (even with attempts).

All guards take an open connection so they run inside the caller's
transaction, *before* the write. They raise ConflictError (→ 409); the
resolver helpers resolve the owning exam_id from a section/question id and
are no-ops when the id doesn't resolve (caller's own not-found handling
then applies).
"""

from services.exceptions import ConflictError


async def assert_exam_content_editable(conn, exam_id) -> None:
    """409 if the exam has any attempt (content frozen to protect attempts)."""
    has_attempts = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.attempts WHERE exam_id = $1)",
        exam_id,
    )
    if has_attempts:
        raise ConflictError(
            "Exam already has attempts; its content can no longer be edited"
        )


async def assert_no_attempts_among(conn, exam_ids) -> None:
    """Batch variant: 409 if ANY exam in the set already has attempts."""
    ids = [e for e in (exam_ids or []) if e is not None]
    if not ids:
        return
    has_attempts = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.attempts "
        "WHERE exam_id = ANY($1::uuid[]))",
        ids,
    )
    if has_attempts:
        raise ConflictError(
            "One or more target items belong to an exam with attempts; "
            "its content can no longer be edited"
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
    await assert_no_attempts_among(conn, [r["exam_id"] for r in rows])


async def assert_questions_editable(conn, question_ids) -> None:
    """Batch guard for a list of question ids."""
    rows = await conn.fetch(
        "SELECT DISTINCT s.exam_id FROM public.questions q "
        "JOIN public.sections s ON s.id = q.section_id WHERE q.id = ANY($1::uuid[])",
        list(question_ids),
    )
    await assert_no_attempts_among(conn, [r["exam_id"] for r in rows])
