"""Delete safety — DL1-DL7. A class deletes only when 0 teachers + 0 students."""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _admin(auth_headers):
    return auth_headers("admin@x.com", role="admin")


async def _exists(db_pool, class_id) -> bool:
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.classes WHERE id=$1)",
            uuid.UUID(class_id),
        )


async def test_delete_empty_class_204(client, auth_headers, make_class, db_pool):
    """DL1"""
    cls = await make_class(name="Empty")
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert r.status_code == 204
    assert not await _exists(db_pool, cls["id"])


async def test_delete_class_with_students_400(
    client, auth_headers, make_user, make_class, db_pool
):
    """DL2 — class kept."""
    student = await make_user(email="dl2-s@x.com", role="student")
    cls = await make_class(name="DL2", student_ids=[student["id"]])
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert r.status_code == 400
    assert "remove all first" in r.json()["detail"]
    assert await _exists(db_pool, cls["id"])


async def test_delete_class_with_teachers_400(
    client, auth_headers, make_user, make_class, db_pool
):
    """DL3"""
    teacher = await make_user(email="dl3-t@x.com", role="teacher")
    cls = await make_class(name="DL3", teacher_ids=[teacher["id"]])
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert r.status_code == 400
    assert await _exists(db_pool, cls["id"])


async def test_delete_class_with_both_400(
    client, auth_headers, make_user, make_class
):
    """DL4"""
    teacher = await make_user(email="dl4-t@x.com", role="teacher")
    student = await make_user(email="dl4-s@x.com", role="student")
    cls = await make_class(
        name="DL4", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert r.status_code == 400


async def test_delete_after_kicking_all_members_succeeds(
    client, auth_headers, make_user, make_class
):
    """DL5"""
    teacher = await make_user(email="dl5-t@x.com", role="teacher")
    student = await make_user(email="dl5-s@x.com", role="student")
    cls = await make_class(
        name="DL5", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )
    h = _admin(auth_headers)
    await client.delete(
        f"/api/admin/classes/{cls['id']}/teachers/{teacher['id']}", headers=h
    )
    await client.delete(
        f"/api/admin/classes/{cls['id']}/students/{student['id']}", headers=h
    )
    r = await client.delete(f"/api/admin/classes/{cls['id']}", headers=h)
    assert r.status_code == 204


async def test_delete_nonexistent_class_404(client, auth_headers):
    """DL6"""
    r = await client.delete(
        f"/api/admin/classes/{uuid.uuid4()}", headers=_admin(auth_headers)
    )
    assert r.status_code == 404


async def test_delete_as_non_admin_403(client, auth_headers, make_class):
    """DL7"""
    cls = await make_class(name="DL7")
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}",
        headers=auth_headers("t@x.com", role="teacher"),
    )
    assert r.status_code == 403
