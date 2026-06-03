"""DB-level guarantees for class membership tables (migration 0013).

CL1-CL5 — UNIQUE(student_id), PK uniqueness, FK CASCADE. Raw SQL against
the live pool. Auto-skipped unless MAICHI_TEST_DB=1.
"""

import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.integration


async def _new_class(conn, name="C") -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO public.classes (name) VALUES ($1) RETURNING id", name
    )


async def test_unique_student_id_blocks_second_class(db_pool, make_user):
    """CL1 — a student already in one class cannot be added to another."""
    student = await make_user(email="cl1-s@x.com", role="student")
    async with db_pool.acquire() as conn:
        a = await _new_class(conn, "A")
        b = await _new_class(conn, "B")
        await conn.execute(
            "INSERT INTO public.class_students (class_id, student_id) VALUES ($1,$2)",
            a, uuid.UUID(student["id"]),
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO public.class_students (class_id, student_id) "
                "VALUES ($1,$2)",
                b, uuid.UUID(student["id"]),
            )


async def test_same_teacher_two_classes_allowed(db_pool, make_user):
    """CL2 — N-N: a teacher may teach multiple classes."""
    teacher = await make_user(email="cl2-t@x.com", role="teacher")
    async with db_pool.acquire() as conn:
        a = await _new_class(conn, "A")
        b = await _new_class(conn, "B")
        for cid in (a, b):
            await conn.execute(
                "INSERT INTO public.class_teachers (class_id, teacher_id) "
                "VALUES ($1,$2)",
                cid, uuid.UUID(teacher["id"]),
            )
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM public.class_teachers WHERE teacher_id=$1",
            uuid.UUID(teacher["id"]),
        )
    assert count == 2


async def test_duplicate_teacher_in_same_class_pk_violation(db_pool, make_user):
    """CL3 — (class_id, teacher_id) PK rejects duplicates."""
    teacher = await make_user(email="cl3-t@x.com", role="teacher")
    async with db_pool.acquire() as conn:
        a = await _new_class(conn, "A")
        await conn.execute(
            "INSERT INTO public.class_teachers (class_id, teacher_id) VALUES ($1,$2)",
            a, uuid.UUID(teacher["id"]),
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO public.class_teachers (class_id, teacher_id) "
                "VALUES ($1,$2)",
                a, uuid.UUID(teacher["id"]),
            )


async def test_delete_class_cascades_membership_rows(db_pool, make_user):
    """CL4 — deleting a class drops its membership rows (housekeeping)."""
    teacher = await make_user(email="cl4-t@x.com", role="teacher")
    student = await make_user(email="cl4-s@x.com", role="student")
    async with db_pool.acquire() as conn:
        a = await _new_class(conn, "A")
        await conn.execute(
            "INSERT INTO public.class_teachers (class_id, teacher_id) VALUES ($1,$2)",
            a, uuid.UUID(teacher["id"]),
        )
        await conn.execute(
            "INSERT INTO public.class_students (class_id, student_id) VALUES ($1,$2)",
            a, uuid.UUID(student["id"]),
        )
        await conn.execute("DELETE FROM public.classes WHERE id=$1", a)
        tc = await conn.fetchval(
            "SELECT COUNT(*) FROM public.class_teachers WHERE class_id=$1", a
        )
        sc = await conn.fetchval(
            "SELECT COUNT(*) FROM public.class_students WHERE class_id=$1", a
        )
    assert tc == 0 and sc == 0


async def test_delete_profile_cascades_membership(db_pool, make_user):
    """CL5 — deleting a user drops their membership rows."""
    student = await make_user(email="cl5-s@x.com", role="student")
    async with db_pool.acquire() as conn:
        a = await _new_class(conn, "A")
        await conn.execute(
            "INSERT INTO public.class_students (class_id, student_id) VALUES ($1,$2)",
            a, uuid.UUID(student["id"]),
        )
        await conn.execute(
            "DELETE FROM public.profiles WHERE id=$1", uuid.UUID(student["id"])
        )
        sc = await conn.fetchval(
            "SELECT COUNT(*) FROM public.class_students WHERE student_id=$1",
            uuid.UUID(student["id"]),
        )
    assert sc == 0
