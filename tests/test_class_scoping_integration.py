"""RBAC scoping integration (class-management §7 / teacher-grading §6 /
attempt-lifecycle §5.7).

Phase 1 shipped the two predicate helpers (SC1/SC2/SC1b/SC2b). Phase 2
wires `teacher_shares_class_with` into the grade + comment endpoints and
the attempt-detail visibility check (route layer; admin bypass). Those are
SC3-SC9 + R3/R6 below.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _admin(auth_headers):
    return auth_headers("admin@x.com", role="admin")


def _teacher(auth_headers, user):
    return auth_headers(user["email"], role="teacher")


async def _set_fully_graded(db_pool, attempt_id, value):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.attempts SET is_fully_graded=$1 WHERE id=$2",
            value, uuid.UUID(attempt_id),
        )


async def _writing_exam(make_exam, n_questions=1):
    """A published exam with one writing section carrying N manual questions."""
    return await make_exam(
        skill="reading",
        sections=[
            {
                "type": "writing",
                "questions": [
                    {
                        "question_type": "writing",
                        "question_data": {"prompt": f"Write essay {i}"},
                        "points": 10,
                    }
                    for i in range(n_questions)
                ],
            }
        ],
    )


async def _gradeable_attempt(make_exam, make_attempt, db_pool, student_id, n=1):
    exam = await _writing_exam(make_exam, n_questions=n)
    attempt = await make_attempt(student_id, exam["id"], state="submitted")
    await _set_fully_graded(db_pool, attempt["id"], False)
    return exam, attempt


# ===================================================================== #
# Helper predicates — phase 1 (still covered here)                       #
# ===================================================================== #


async def test_teacher_shares_class_with_true_when_same_class(
    db, make_user, make_class
):
    """SC1"""
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
    await make_class(name="SC2-T", teacher_ids=[teacher["id"]])
    await make_class(name="SC2-S", student_ids=[student["id"]])
    assert await class_service.teacher_shares_class_with(
        teacher["id"], student["id"]
    ) is False


async def test_teacher_teaches_class_true_when_member(db, make_user, make_class):
    """SC1b"""
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
    cls = await make_class(name="SC2b")
    assert await class_service.teacher_teaches_class(
        teacher["id"], cls["id"]
    ) is False


# ===================================================================== #
# Grade scoping — SC3/SC4/SC5                                            #
# ===================================================================== #


async def test_grade_403_when_teacher_not_in_students_class(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """SC3 — teacher grading a student outside their class → 403."""
    teacher = await make_user(email="sc3-t@x.com", role="teacher")
    student = await make_user(email="sc3-s@x.com", role="student")
    # teacher teaches some class; student is in a different one
    await make_class(name="SC3-T", teacher_ids=[teacher["id"]])
    await make_class(name="SC3-S", student_ids=[student["id"]])
    exam, attempt = await _gradeable_attempt(
        make_exam, make_attempt, db_pool, student["id"]
    )
    qid = exam["sections"][0]["questions"][0]["id"]

    r = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/grade",
        headers=_teacher(auth_headers, teacher),
        json={"grades": [{"questionId": qid, "pointsEarned": 5}]},
    )
    assert r.status_code == 403


async def test_grade_200_when_teacher_teaches_students_class(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """SC4 — teacher grading a student in their class → 200."""
    teacher = await make_user(email="sc4-t@x.com", role="teacher")
    student = await make_user(email="sc4-s@x.com", role="student")
    await make_class(
        name="SC4", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam, attempt = await _gradeable_attempt(
        make_exam, make_attempt, db_pool, student["id"]
    )
    qid = exam["sections"][0]["questions"][0]["id"]

    r = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/grade",
        headers=_teacher(auth_headers, teacher),
        json={"grades": [{"questionId": qid, "pointsEarned": 8}]},
    )
    assert r.status_code == 200
    assert r.json()["data"]["isFullyGraded"] is True


async def test_admin_can_grade_any_attempt_bypass_class(
    client, auth_headers, make_user, make_exam, make_attempt, db_pool
):
    """SC5 — admin grades a student in no class of theirs → 200."""
    student = await make_user(email="sc5-s@x.com", role="student")
    exam, attempt = await _gradeable_attempt(
        make_exam, make_attempt, db_pool, student["id"]
    )
    qid = exam["sections"][0]["questions"][0]["id"]

    r = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/grade",
        headers=_admin(auth_headers),
        json={"grades": [{"questionId": qid, "pointsEarned": 7}]},
    )
    assert r.status_code == 200


# ===================================================================== #
# Comment scoping — SC6                                                  #
# ===================================================================== #


async def test_writing_comment_403_when_not_in_class(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """SC6 — teacher commenting on a student outside their class → 403.
    The route-layer scope guard fires before any answer lookup."""
    teacher = await make_user(email="sc6-t@x.com", role="teacher")
    student = await make_user(email="sc6-s@x.com", role="student")
    await make_class(name="SC6-T", teacher_ids=[teacher["id"]])
    await make_class(name="SC6-S", student_ids=[student["id"]])
    _, attempt = await _gradeable_attempt(
        make_exam, make_attempt, db_pool, student["id"]
    )

    r = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/answers/{uuid.uuid4()}"
        "/writing-comments",
        headers=_teacher(auth_headers, teacher),
        json={
            "rangeStart": 0,
            "rangeEnd": 4,
            "quotedText": "test",
            "commentText": "nope",
        },
    )
    assert r.status_code == 403


# ===================================================================== #
# Attempt detail scoping — SC7/SC8/SC9                                   #
# ===================================================================== #


async def test_attempt_detail_teacher_403_when_not_in_class(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SC7"""
    teacher = await make_user(email="sc7-t@x.com", role="teacher")
    student = await make_user(email="sc7-s@x.com", role="student")
    await make_class(name="SC7-T", teacher_ids=[teacher["id"]])
    await make_class(name="SC7-S", student_ids=[student["id"]])
    exam = await make_exam()
    attempt = await make_attempt(student["id"], exam["id"], state="submitted")

    r = await client.get(
        f"/api/attempts/{attempt['id']}", headers=_teacher(auth_headers, teacher)
    )
    assert r.status_code == 403


async def test_attempt_detail_teacher_200_when_in_class(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SC8"""
    teacher = await make_user(email="sc8-t@x.com", role="teacher")
    student = await make_user(email="sc8-s@x.com", role="student")
    await make_class(
        name="SC8", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    attempt = await make_attempt(student["id"], exam["id"], state="submitted")

    r = await client.get(
        f"/api/attempts/{attempt['id']}", headers=_teacher(auth_headers, teacher)
    )
    assert r.status_code == 200


async def test_attempt_detail_owner_admin_parent_unaffected(
    client, auth_headers, make_user, make_exam, make_attempt
):
    """SC9 — owner, admin, and linked parent retain access regardless of class."""
    parent = await make_user(email="sc9-p@x.com", role="parent")
    student = await make_user(
        email="sc9-s@x.com", role="student", parent_id=parent["id"]
    )
    # GET /api/attempts/{id} resolves the viewer's profile by email for EVERY
    # role (incl. admin), so the admin viewer needs a real DB row — seed one
    # rather than using a token-only admin email.
    admin_user = await make_user(email="sc9-admin@x.com", role="admin")
    exam = await make_exam()
    attempt = await make_attempt(student["id"], exam["id"], state="submitted")

    owner = await client.get(
        f"/api/attempts/{attempt['id']}",
        headers=auth_headers(student["email"], role="student"),
    )
    admin = await client.get(
        f"/api/attempts/{attempt['id']}",
        headers=auth_headers(admin_user["email"], role="admin"),
    )
    par = await client.get(
        f"/api/attempts/{attempt['id']}",
        headers=auth_headers(parent["email"], role="parent"),
    )
    assert owner.status_code == 200
    assert admin.status_code == 200
    assert par.status_code == 200


# ===================================================================== #
# Real-world — R3 / R6                                                   #
# ===================================================================== #


async def test_remove_teacher_then_loses_access_to_grade(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """R3 — after a teacher is removed from the class, grading 403s."""
    teacher = await make_user(email="r3-t@x.com", role="teacher")
    student = await make_user(email="r3-s@x.com", role="student")
    cls = await make_class(
        name="R3", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam, attempt = await _gradeable_attempt(
        make_exam, make_attempt, db_pool, student["id"]
    )
    qid = exam["sections"][0]["questions"][0]["id"]

    # kick the teacher out of the class
    await client.delete(
        f"/api/admin/classes/{cls['id']}/teachers/{teacher['id']}",
        headers=_admin(auth_headers),
    )

    r = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/grade",
        headers=_teacher(auth_headers, teacher),
        json={"grades": [{"questionId": qid, "pointsEarned": 5}]},
    )
    assert r.status_code == 403


async def test_two_teachers_same_class_both_can_grade(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """R6 — two teachers of one class can each grade its students' work."""
    t1 = await make_user(email="r6-t1@x.com", role="teacher")
    t2 = await make_user(email="r6-t2@x.com", role="teacher")
    student = await make_user(email="r6-s@x.com", role="student")
    await make_class(
        name="R6", teacher_ids=[t1["id"], t2["id"]], student_ids=[student["id"]]
    )
    exam, attempt = await _gradeable_attempt(
        make_exam, make_attempt, db_pool, student["id"], n=2
    )
    q1 = exam["sections"][0]["questions"][0]["id"]
    q2 = exam["sections"][0]["questions"][1]["id"]

    r1 = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/grade",
        headers=_teacher(auth_headers, t1),
        json={"grades": [{"questionId": q1, "pointsEarned": 6}]},
    )
    r2 = await client.post(
        f"/api/teacher/attempts/{attempt['id']}/grade",
        headers=_teacher(auth_headers, t2),
        json={"grades": [{"questionId": q2, "pointsEarned": 9}]},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["data"]["isFullyGraded"] is True
