"""Teacher class listing + submissions — TC1-TC5, SB1-SB11, plus the
foundation-level real-world cases R1/R2/R4/R5/R7.

The grade/comment-scoping real-world cases (R3, R6) depend on the phase-2
RBAC amendments to teacher-grading and live in test_class_scoping_integration.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def _admin(auth_headers):
    return auth_headers("admin@x.com", role="admin")


def _teacher(auth_headers, user):
    return auth_headers(user["email"], role="teacher")


async def _set_attempt(db_pool, attempt_id, *, fully_graded=None, submitted_at=None):
    sets, vals = [], []
    if fully_graded is not None:
        vals.append(fully_graded)
        sets.append(f"is_fully_graded = ${len(vals)}")
    if submitted_at is not None:
        vals.append(submitted_at)
        sets.append(f"submitted_at = ${len(vals)}")
    if not sets:
        return
    vals.append(uuid.UUID(attempt_id))
    async with db_pool.acquire() as conn:
        await conn.execute(
            f"UPDATE public.attempts SET {', '.join(sets)} WHERE id=${len(vals)}",
            *vals,
        )


# ===================================================================== #
# GET /api/teacher/classes                                              #
# ===================================================================== #


async def test_teacher_sees_only_classes_they_teach(
    client, auth_headers, make_user, make_class
):
    """TC1 — teacher attached to 2 of 5 classes sees exactly those 2."""
    teacher = await make_user(email="tc1-t@x.com", role="teacher")
    mine = []
    for i in range(5):
        cls = await make_class(
            name=f"TC1-{i}",
            teacher_ids=[teacher["id"]] if i < 2 else [],
        )
        if i < 2:
            mine.append(cls["id"])

    r = await client.get("/api/teacher/classes", headers=_teacher(auth_headers, teacher))
    assert r.status_code == 200
    got = {c["id"] for c in r.json()["data"]["items"]}
    assert got == set(mine)


async def test_teacher_with_no_classes_empty_list(
    client, auth_headers, make_user
):
    """TC2"""
    teacher = await make_user(email="tc2-t@x.com", role="teacher")
    r = await client.get("/api/teacher/classes", headers=_teacher(auth_headers, teacher))
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []


async def test_admin_sees_all_classes(client, auth_headers, make_class):
    """TC3"""
    await make_class(name="TC3-A")
    await make_class(name="TC3-B")
    r = await client.get("/api/teacher/classes", headers=_admin(auth_headers))
    assert r.status_code == 200
    names = {c["name"] for c in r.json()["data"]["items"]}
    assert {"TC3-A", "TC3-B"} <= names


async def test_teacher_classes_include_student_count(
    client, auth_headers, make_user, make_class
):
    """TC4"""
    teacher = await make_user(email="tc4-t@x.com", role="teacher")
    s1 = await make_user(email="tc4-s1@x.com", role="student")
    s2 = await make_user(email="tc4-s2@x.com", role="student")
    await make_class(
        name="TC4", teacher_ids=[teacher["id"]], student_ids=[s1["id"], s2["id"]]
    )
    r = await client.get("/api/teacher/classes", headers=_teacher(auth_headers, teacher))
    item = next(c for c in r.json()["data"]["items"] if c["name"] == "TC4")
    assert item["studentCount"] == 2


async def test_teacher_classes_include_pending_grading_count(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """TC4b — counts only submitted, non-abandoned, not-fully-graded attempts."""
    teacher = await make_user(email="tc4b-t@x.com", role="teacher")
    student = await make_user(email="tc4b-s@x.com", role="student")
    cls = await make_class(
        name="TC4b", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()

    # pending (submitted + ungraded)
    pending = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, pending["id"], fully_graded=False)
    # graded (submitted + fully graded) → not counted
    graded = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, graded["id"], fully_graded=True)
    # abandoned → not counted
    await make_attempt(student["id"], exam["id"], state="abandoned")

    r = await client.get("/api/teacher/classes", headers=_teacher(auth_headers, teacher))
    item = next(c for c in r.json()["data"]["items"] if c["name"] == "TC4b")
    assert item["pendingGradingCount"] == 1


async def test_student_role_403_on_teacher_classes(client, auth_headers):
    """TC5"""
    r = await client.get(
        "/api/teacher/classes", headers=auth_headers("s@x.com", role="student")
    )
    assert r.status_code == 403


# ===================================================================== #
# GET /api/teacher/classes/{id}/submissions                            #
# ===================================================================== #


async def test_submissions_lists_submitted_attempts_of_class_students(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SB1"""
    teacher = await make_user(email="sb1-t@x.com", role="teacher")
    student = await make_user(email="sb1-s@x.com", role="student")
    cls = await make_class(
        name="SB1", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    att = await make_attempt(student["id"], exam["id"], state="submitted")

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    assert r.status_code == 200
    ids = [s["attemptId"] for s in r.json()["data"]["items"]]
    assert att["id"] in ids


async def test_submissions_excludes_abandoned(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SB2"""
    teacher = await make_user(email="sb2-t@x.com", role="teacher")
    student = await make_user(email="sb2-s@x.com", role="student")
    cls = await make_class(
        name="SB2", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    ab = await make_attempt(student["id"], exam["id"], state="abandoned")
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    assert ab["id"] not in [s["attemptId"] for s in r.json()["data"]["items"]]


async def test_submissions_excludes_in_progress(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SB3"""
    teacher = await make_user(email="sb3-t@x.com", role="teacher")
    student = await make_user(email="sb3-s@x.com", role="student")
    cls = await make_class(
        name="SB3", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    ip = await make_attempt(student["id"], exam["id"], state="in_progress")
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    assert ip["id"] not in [s["attemptId"] for s in r.json()["data"]["items"]]


async def test_submissions_status_all_default(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """SB4 — default returns both graded and pending submitted attempts."""
    teacher = await make_user(email="sb4-t@x.com", role="teacher")
    student = await make_user(email="sb4-s@x.com", role="student")
    cls = await make_class(
        name="SB4", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    pending = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, pending["id"], fully_graded=False)
    graded = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, graded["id"], fully_graded=True)

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    ids = {s["attemptId"] for s in r.json()["data"]["items"]}
    assert {pending["id"], graded["id"]} <= ids


async def test_submissions_status_pending_filters_fully_graded(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """SB5"""
    teacher = await make_user(email="sb5-t@x.com", role="teacher")
    student = await make_user(email="sb5-s@x.com", role="student")
    cls = await make_class(
        name="SB5", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    pending = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, pending["id"], fully_graded=False)
    graded = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, graded["id"], fully_graded=True)

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions?status=pending",
        headers=_teacher(auth_headers, teacher),
    )
    ids = {s["attemptId"] for s in r.json()["data"]["items"]}
    assert pending["id"] in ids
    assert graded["id"] not in ids


async def test_submissions_excludes_other_class_students(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SB6"""
    teacher = await make_user(email="sb6-t@x.com", role="teacher")
    mine = await make_user(email="sb6-s1@x.com", role="student")
    other = await make_user(email="sb6-s2@x.com", role="student")
    cls = await make_class(
        name="SB6-mine", teacher_ids=[teacher["id"]], student_ids=[mine["id"]]
    )
    await make_class(name="SB6-other", student_ids=[other["id"]])
    exam = await make_exam()
    await make_attempt(mine["id"], exam["id"], state="submitted")
    other_att = await make_attempt(other["id"], exam["id"], state="submitted")

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    assert other_att["id"] not in [s["attemptId"] for s in r.json()["data"]["items"]]


async def test_submissions_403_when_teacher_not_in_class(
    client, auth_headers, make_user, make_class
):
    """SB7"""
    teacher = await make_user(email="sb7-t@x.com", role="teacher")
    cls = await make_class(name="SB7")  # teacher not attached
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    assert r.status_code == 403


async def test_submissions_admin_bypass_any_class(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SB8"""
    student = await make_user(email="sb8-s@x.com", role="student")
    cls = await make_class(name="SB8", student_ids=[student["id"]])
    exam = await make_exam()
    att = await make_attempt(student["id"], exam["id"], state="submitted")
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_admin(auth_headers),
    )
    assert r.status_code == 200
    assert att["id"] in [s["attemptId"] for s in r.json()["data"]["items"]]


async def test_submissions_404_class_not_found(client, auth_headers, make_user):
    """SB9 — missing class → 404 (not 403), checked before teaching guard."""
    teacher = await make_user(email="sb9-t@x.com", role="teacher")
    r = await client.get(
        f"/api/teacher/classes/{uuid.uuid4()}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    assert r.status_code == 404


async def test_submissions_item_shape(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """SB10"""
    teacher = await make_user(email="sb10-t@x.com", role="teacher")
    student = await make_user(email="sb10-s@x.com", role="student", full_name="Pupil")
    cls = await make_class(
        name="SB10", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam(title="Exam X", level="KET", skill="reading")
    await make_attempt(student["id"], exam["id"], state="submitted")

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    item = r.json()["data"]["items"][0]
    assert set(item) >= {
        "attemptId", "student", "exam", "submittedAt", "isFullyGraded", "score"
    }
    assert item["student"]["fullName"] == "Pupil"
    assert item["exam"]["title"] == "Exam X"
    assert item["exam"]["skill"] == "reading"


async def test_submissions_ordered_by_submitted_at_desc(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """SB11"""
    teacher = await make_user(email="sb11-t@x.com", role="teacher")
    student = await make_user(email="sb11-s@x.com", role="student")
    cls = await make_class(
        name="SB11", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    exam = await make_exam()
    now = datetime.now(timezone.utc)
    older = await make_attempt(student["id"], exam["id"], state="submitted")
    newer = await make_attempt(student["id"], exam["id"], state="submitted")
    await _set_attempt(db_pool, older["id"], submitted_at=now - timedelta(hours=2))
    await _set_attempt(db_pool, newer["id"], submitted_at=now)

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}/submissions",
        headers=_teacher(auth_headers, teacher),
    )
    ids = [s["attemptId"] for s in r.json()["data"]["items"]]
    assert ids.index(newer["id"]) < ids.index(older["id"])


# ===================================================================== #
# Real-world (foundation-level)                                         #
# ===================================================================== #


async def test_teacher_in_two_classes_switches_and_sees_correct_submissions(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """R1"""
    teacher = await make_user(email="r1-t@x.com", role="teacher")
    sa = await make_user(email="r1-sa@x.com", role="student")
    sb = await make_user(email="r1-sb@x.com", role="student")
    ca = await make_class(name="R1-A", teacher_ids=[teacher["id"]], student_ids=[sa["id"]])
    cb = await make_class(name="R1-B", teacher_ids=[teacher["id"]], student_ids=[sb["id"]])
    exam = await make_exam()
    aa = await make_attempt(sa["id"], exam["id"], state="submitted")
    ab = await make_attempt(sb["id"], exam["id"], state="submitted")

    h = _teacher(auth_headers, teacher)
    ra = await client.get(f"/api/teacher/classes/{ca['id']}/submissions", headers=h)
    rb = await client.get(f"/api/teacher/classes/{cb['id']}/submissions", headers=h)
    assert [s["attemptId"] for s in ra.json()["data"]["items"]] == [aa["id"]]
    assert [s["attemptId"] for s in rb.json()["data"]["items"]] == [ab["id"]]


async def test_student_moved_between_classes_submissions_follow(
    client, auth_headers, make_user, make_class, make_exam, make_attempt
):
    """R2 — move student A→B; their attempt now shows under B, not A."""
    teacher = await make_user(email="r2-t@x.com", role="teacher")
    student = await make_user(email="r2-s@x.com", role="student")
    ca = await make_class(name="R2-A", teacher_ids=[teacher["id"]], student_ids=[student["id"]])
    cb = await make_class(name="R2-B", teacher_ids=[teacher["id"]])
    exam = await make_exam()
    att = await make_attempt(student["id"], exam["id"], state="submitted")

    h = _admin(auth_headers)
    await client.delete(
        f"/api/admin/classes/{ca['id']}/students/{student['id']}", headers=h
    )
    await client.post(
        f"/api/admin/classes/{cb['id']}/students",
        headers=h,
        json={"studentId": student["id"]},
    )

    th = _teacher(auth_headers, teacher)
    ra = await client.get(f"/api/teacher/classes/{ca['id']}/submissions", headers=th)
    rb = await client.get(f"/api/teacher/classes/{cb['id']}/submissions", headers=th)
    assert att["id"] not in [s["attemptId"] for s in ra.json()["data"]["items"]]
    assert att["id"] in [s["attemptId"] for s in rb.json()["data"]["items"]]


async def test_remove_student_keeps_their_past_attempts(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """R4 — removing a student from a class doesn't delete their attempts."""
    student = await make_user(email="r4-s@x.com", role="student")
    cls = await make_class(name="R4", student_ids=[student["id"]])
    exam = await make_exam()
    att = await make_attempt(student["id"], exam["id"], state="submitted")

    await client.delete(
        f"/api/admin/classes/{cls['id']}/students/{student['id']}",
        headers=_admin(auth_headers),
    )
    async with db_pool.acquire() as conn:
        still = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.attempts WHERE id=$1)",
            uuid.UUID(att["id"]),
        )
    assert still


async def test_delete_class_does_not_touch_attempts(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """R5 — deleting an (emptied) class leaves former students' attempts intact."""
    student = await make_user(email="r5-s@x.com", role="student")
    cls = await make_class(name="R5", student_ids=[student["id"]])
    exam = await make_exam()
    att = await make_attempt(student["id"], exam["id"], state="submitted")

    h = _admin(auth_headers)
    await client.delete(
        f"/api/admin/classes/{cls['id']}/students/{student['id']}", headers=h
    )
    r = await client.delete(f"/api/admin/classes/{cls['id']}", headers=h)
    assert r.status_code == 204
    async with db_pool.acquire() as conn:
        still = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.attempts WHERE id=$1)",
            uuid.UUID(att["id"]),
        )
    assert still


async def test_teacher_role_changed_loses_endpoint_access(client, auth_headers):
    """R7 — a caller whose token role isn't teacher/admin is rejected by the
    router guard (the actual enforcement mechanism)."""
    r = await client.get(
        "/api/teacher/classes", headers=auth_headers("ex-teacher@x.com", role="student")
    )
    assert r.status_code == 403
