"""Class-management v2 — teacher class-detail (TD*) + student my-classes (ME*).

Integration tests; auto-skipped unless the integration DB is enabled.
Covers docs/class-management/class-management-testcases.md §10b + §10c.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _admin(auth_headers):
    return auth_headers("admin@x.com", role="admin")


def _teacher(auth_headers, user):
    return auth_headers(user["email"], role="teacher")


def _student(auth_headers, user):
    return auth_headers(user["email"], role="student")


async def _grade_attempt(db_pool, attempt_id, *, percentage, fully_graded=True):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.attempts SET percentage=$1, is_fully_graded=$2 "
            "WHERE id=$3",
            percentage, fully_graded, uuid.UUID(attempt_id),
        )


# ===================================================================== #
# Teacher class-detail (TD*)                                            #
# ===================================================================== #


async def test_class_detail_returns_roster_and_coteachers(
    client, auth_headers, make_user, make_class
):
    """TD1 — roster (students) + co-teachers for a teaching teacher."""
    t1 = await make_user(email="td1-t1@x.com", role="teacher")
    t2 = await make_user(email="td1-t2@x.com", role="teacher")
    s1 = await make_user(email="td1-s1@x.com", role="student")
    cls = await make_class(
        name="TD1", teacher_ids=[t1["id"], t2["id"]], student_ids=[s1["id"]]
    )
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}", headers=_teacher(auth_headers, t1)
    )
    assert r.status_code == 200
    data = r.json()["data"]["class"]
    assert {t["id"] for t in data["teachers"]} == {t1["id"], t2["id"]}
    assert s1["id"] in [s["id"] for s in data["students"]]


async def test_class_detail_coteachers_have_no_email(
    client, auth_headers, make_user, make_class
):
    """TD9 — co-teacher projection excludes email."""
    t1 = await make_user(email="td9-t1@x.com", role="teacher")
    cls = await make_class(name="TD9", teacher_ids=[t1["id"]])
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}", headers=_teacher(auth_headers, t1)
    )
    assert r.status_code == 200
    for t in r.json()["data"]["class"]["teachers"]:
        assert "email" not in t


async def test_class_detail_progress_avg_only_fully_graded(
    client, auth_headers, make_user, make_class, make_exam, make_attempt, db_pool
):
    """TD2/TD3 — submitted/pending counts + avg over fully-graded only."""
    t1 = await make_user(email="td3-t@x.com", role="teacher")
    s1 = await make_user(email="td3-s@x.com", role="student")
    cls = await make_class(
        name="TD3", teacher_ids=[t1["id"]], student_ids=[s1["id"]]
    )
    exam = await make_exam()

    graded = await make_attempt(s1["id"], exam["id"], state="submitted")
    await _grade_attempt(db_pool, graded["id"], percentage=80, fully_graded=True)
    pending = await make_attempt(s1["id"], exam["id"], state="submitted")
    await _grade_attempt(db_pool, pending["id"], percentage=0, fully_graded=False)

    r = await client.get(
        f"/api/teacher/classes/{cls['id']}", headers=_teacher(auth_headers, t1)
    )
    stud = next(
        s for s in r.json()["data"]["class"]["students"] if s["id"] == s1["id"]
    )
    assert stud["submittedCount"] == 2
    assert stud["pendingGradingCount"] == 1
    assert stud["averagePercentage"] == 80.0  # only the fully-graded attempt
    assert stud["lastSubmittedAt"] is not None


async def test_class_detail_avg_null_when_no_graded(
    client, auth_headers, make_user, make_class
):
    """TD4 — averagePercentage is null when the student has no graded attempt."""
    t1 = await make_user(email="td4-t@x.com", role="teacher")
    s1 = await make_user(email="td4-s@x.com", role="student")
    cls = await make_class(
        name="TD4", teacher_ids=[t1["id"]], student_ids=[s1["id"]]
    )
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}", headers=_teacher(auth_headers, t1)
    )
    stud = next(
        s for s in r.json()["data"]["class"]["students"] if s["id"] == s1["id"]
    )
    assert stud["averagePercentage"] is None
    assert stud["submittedCount"] == 0


async def test_class_detail_403_when_not_teaching(
    client, auth_headers, make_user, make_class
):
    """TD6 — a teacher who doesn't teach the class gets 403."""
    owner = await make_user(email="td6-own@x.com", role="teacher")
    other = await make_user(email="td6-oth@x.com", role="teacher")
    cls = await make_class(name="TD6", teacher_ids=[owner["id"]])
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}", headers=_teacher(auth_headers, other)
    )
    assert r.status_code == 403


async def test_class_detail_admin_bypass_200(
    client, auth_headers, make_user, make_class
):
    """TD7 — admin sees any class detail."""
    t1 = await make_user(email="td7-t@x.com", role="teacher")
    cls = await make_class(name="TD7", teacher_ids=[t1["id"]])
    r = await client.get(
        f"/api/teacher/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert r.status_code == 200


async def test_class_detail_404_not_found(client, auth_headers):
    """TD8 — missing class → 404 (admin path so it isn't masked as 403)."""
    r = await client.get(
        f"/api/teacher/classes/{uuid.uuid4()}", headers=_admin(auth_headers)
    )
    assert r.status_code == 404


# ===================================================================== #
# Student my-classes (ME*)                                             #
# ===================================================================== #


async def test_me_classes_lists_only_my_classes(
    client, auth_headers, make_user, make_class
):
    """ME1 — student sees only classes they belong to."""
    s1 = await make_user(email="me1-s@x.com", role="student")
    mine = await make_class(name="ME1-mine", student_ids=[s1["id"]])
    await make_class(name="ME1-other")  # not a member
    r = await client.get("/api/me/classes", headers=_student(auth_headers, s1))
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["data"]["items"]]
    assert ids == [mine["id"]]


async def test_me_classes_empty_when_no_membership(
    client, auth_headers, make_user
):
    """ME2 — no membership → empty list."""
    s1 = await make_user(email="me2-s@x.com", role="student")
    r = await client.get("/api/me/classes", headers=_student(auth_headers, s1))
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []


async def test_me_class_detail_teachers_with_email_classmates_without(
    client, auth_headers, make_user, make_class
):
    """ME3/ME4/ME5 — teachers carry email; classmates are name-only and
    exclude the caller."""
    s1 = await make_user(email="me3-s1@x.com", role="student")
    s2 = await make_user(email="me3-s2@x.com", role="student")
    t1 = await make_user(email="me3-t@x.com", role="teacher")
    cls = await make_class(
        name="ME3", teacher_ids=[t1["id"]], student_ids=[s1["id"], s2["id"]]
    )
    r = await client.get(
        f"/api/me/classes/{cls['id']}", headers=_student(auth_headers, s1)
    )
    assert r.status_code == 200
    data = r.json()["data"]["class"]
    assert data["teachers"][0]["email"] == t1["email"]
    mates = data["classmates"]
    assert {m["id"] for m in mates} == {s2["id"]}      # self excluded
    assert all("email" not in m for m in mates)        # name only


async def test_me_class_detail_404_when_not_member(
    client, auth_headers, make_user, make_class
):
    """ME6 — a class the student isn't in → 404 (existence not leaked)."""
    s1 = await make_user(email="me6-s@x.com", role="student")
    cls = await make_class(name="ME6")  # student not a member
    r = await client.get(
        f"/api/me/classes/{cls['id']}", headers=_student(auth_headers, s1)
    )
    assert r.status_code == 404


async def test_me_classes_requires_student_role(client, auth_headers, make_user):
    """ME7 — a non-student caller is rejected (403)."""
    t1 = await make_user(email="me7-t@x.com", role="teacher")
    r = await client.get("/api/me/classes", headers=_teacher(auth_headers, t1))
    assert r.status_code == 403
