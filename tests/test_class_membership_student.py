"""Student membership — MS1-MS8 + R8 (v2: a student may join MANY classes)."""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integration


def _admin(auth_headers):
    return auth_headers("admin@x.com", role="admin")


async def _add_student(client, auth_headers, class_id, student_id):
    return await client.post(
        f"/api/admin/classes/{class_id}/students",
        headers=_admin(auth_headers),
        json={"studentId": student_id},
    )


async def test_add_student_to_class_201(client, auth_headers, make_user, make_class):
    """MS1"""
    student = await make_user(email="ms1-s@x.com", role="student")
    cls = await make_class(name="MS1")
    r = await _add_student(client, auth_headers, cls["id"], student["id"])
    assert r.status_code == 201
    assert student["id"] in [
        s["id"] for s in r.json()["data"]["class"]["students"]
    ]


async def test_add_student_validates_role_400(
    client, auth_headers, make_user, make_class
):
    """MS2"""
    teacher = await make_user(email="ms2-t@x.com", role="teacher")
    cls = await make_class(name="MS2")
    r = await _add_student(client, auth_headers, cls["id"], teacher["id"])
    assert r.status_code == 400


async def test_add_student_to_second_class_succeeds(
    client, auth_headers, make_user, make_class
):
    """MS3 (v2) — a student may belong to multiple classes at once."""
    student = await make_user(email="ms3-s@x.com", role="student")
    a = await make_class(name="Alpha")
    b = await make_class(name="Beta")
    r1 = await _add_student(client, auth_headers, a["id"], student["id"])
    assert r1.status_code == 201

    r2 = await _add_student(client, auth_headers, b["id"], student["id"])
    assert r2.status_code == 201
    assert student["id"] in [
        s["id"] for s in r2.json()["data"]["class"]["students"]
    ]


async def test_add_student_already_in_same_class_409(
    client, auth_headers, make_user, make_class
):
    """MS4 — re-adding into the same class → 409 (idempotent-ish)."""
    student = await make_user(email="ms4-s@x.com", role="student")
    cls = await make_class(name="MS4", student_ids=[student["id"]])
    r = await _add_student(client, auth_headers, cls["id"], student["id"])
    assert r.status_code == 409


async def test_remove_from_one_class_keeps_other(
    client, auth_headers, make_user, make_class
):
    """MS5 (v2) — student in two classes; removing from one keeps the other."""
    student = await make_user(email="ms5-s@x.com", role="student")
    a = await make_class(name="Keep", student_ids=[student["id"]])
    b = await make_class(name="Drop", student_ids=[student["id"]])

    rm = await client.delete(
        f"/api/admin/classes/{b['id']}/students/{student['id']}",
        headers=_admin(auth_headers),
    )
    assert rm.status_code == 204

    # Still a member of class A.
    detail = await client.get(
        f"/api/admin/classes/{a['id']}", headers=_admin(auth_headers)
    )
    assert student["id"] in [
        s["id"] for s in detail.json()["data"]["class"]["students"]
    ]


async def test_remove_student_204(client, auth_headers, make_user, make_class):
    """MS6"""
    student = await make_user(email="ms6-s@x.com", role="student")
    cls = await make_class(name="MS6", student_ids=[student["id"]])
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}/students/{student['id']}",
        headers=_admin(auth_headers),
    )
    assert r.status_code == 204


async def test_remove_student_not_member_404(
    client, auth_headers, make_user, make_class
):
    """MS7"""
    student = await make_user(email="ms7-s@x.com", role="student")
    cls = await make_class(name="MS7")
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}/students/{student['id']}",
        headers=_admin(auth_headers),
    )
    assert r.status_code == 404


async def test_add_student_nonexistent_user_404(client, auth_headers, make_class):
    """MS8"""
    cls = await make_class(name="MS8")
    r = await _add_student(client, auth_headers, cls["id"], str(uuid.uuid4()))
    assert r.status_code == 404


async def test_concurrent_add_same_student_to_two_classes_both_succeed(
    client, auth_headers, make_user, make_class
):
    """R8 (v2) — two parallel adds into different classes both succeed now
    that UNIQUE(student_id) is gone (multi-class)."""
    student = await make_user(email="r8-s@x.com", role="student")
    a = await make_class(name="R8A")
    b = await make_class(name="R8B")

    results = await asyncio.gather(
        _add_student(client, auth_headers, a["id"], student["id"]),
        _add_student(client, auth_headers, b["id"], student["id"]),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 201]
