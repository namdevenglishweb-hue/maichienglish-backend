"""RBAC scoping — the two predicate helpers (SC1/SC2/SC1b/SC2b) are live in
phase 1. The integration of those helpers into teacher-grading
(grade/comment) and attempt-detail visibility (SC3-SC9, R3, R6) is phase 2
and is marked xfail/skip here so the suite documents the gap without
falsely passing.

See docs/class-management/class-management-design.md §7.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

# Phase-2 work: amend grade/comment/attempt-detail to call the helpers.
_PHASE2 = "class-scoping of grade/comment/attempt-detail is phase 2 (design §7)"


# ===================================================================== #
# Helper predicates — implemented in phase 1                            #
# ===================================================================== #


async def test_teacher_shares_class_with_true_when_same_class(
    db, make_user, make_class
):
    """SC1 — per-student predicate true when teacher teaches student's class."""
    from services.class_service import class_service

    teacher = await make_user(email="sc1-t@x.com", role="teacher")
    student = await make_user(email="sc1-s@x.com", role="student")
    await make_class(
        name="SC1", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    assert await class_service.teacher_shares_class_with(
        teacher["id"], student["id"]
    ) is True


async def test_teacher_shares_class_with_false_when_no_common_class(
    db, make_user, make_class
):
    """SC2"""
    from services.class_service import class_service

    teacher = await make_user(email="sc2-t@x.com", role="teacher")
    student = await make_user(email="sc2-s@x.com", role="student")
    # teacher teaches one class; student belongs to a different one
    await make_class(name="SC2-T", teacher_ids=[teacher["id"]])
    await make_class(name="SC2-S", student_ids=[student["id"]])
    assert await class_service.teacher_shares_class_with(
        teacher["id"], student["id"]
    ) is False


async def test_teacher_teaches_class_true_when_member(db, make_user, make_class):
    """SC1b — per-class predicate true when teacher ∈ class_teachers."""
    from services.class_service import class_service

    teacher = await make_user(email="sc1b-t@x.com", role="teacher")
    cls = await make_class(name="SC1b", teacher_ids=[teacher["id"]])
    assert await class_service.teacher_teaches_class(
        teacher["id"], cls["id"]
    ) is True


async def test_teacher_teaches_class_false_when_not_member(
    db, make_user, make_class
):
    """SC2b"""
    from services.class_service import class_service

    teacher = await make_user(email="sc2b-t@x.com", role="teacher")
    cls = await make_class(name="SC2b")  # teacher not attached
    assert await class_service.teacher_teaches_class(
        teacher["id"], cls["id"]
    ) is False


# ===================================================================== #
# Phase-2 integration — not wired yet (skipped, not silently passing)   #
# ===================================================================== #


@pytest.mark.skip(reason=_PHASE2)
async def test_grade_403_when_teacher_not_in_students_class():
    """SC3"""


@pytest.mark.skip(reason=_PHASE2)
async def test_grade_200_when_teacher_teaches_students_class():
    """SC4"""


@pytest.mark.skip(reason=_PHASE2)
async def test_admin_can_grade_any_attempt_bypass_class():
    """SC5"""


@pytest.mark.skip(reason=_PHASE2)
async def test_writing_comment_403_when_not_in_class():
    """SC6"""


@pytest.mark.skip(reason=_PHASE2)
async def test_attempt_detail_teacher_403_when_not_in_class():
    """SC7"""


@pytest.mark.skip(reason=_PHASE2)
async def test_attempt_detail_teacher_200_when_in_class():
    """SC8"""


@pytest.mark.skip(reason=_PHASE2)
async def test_attempt_detail_owner_admin_parent_unaffected():
    """SC9"""


@pytest.mark.skip(reason=_PHASE2)
async def test_remove_teacher_then_loses_access_to_submissions_and_grade():
    """R3 — submissions part is covered in test_class_teacher_listing via SB7;
    the grade-access half is phase 2."""


@pytest.mark.skip(reason=_PHASE2)
async def test_two_teachers_same_class_both_can_grade():
    """R6"""
