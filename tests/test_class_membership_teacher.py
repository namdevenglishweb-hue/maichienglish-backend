"""Teacher membership — MT1-MT9. /api/admin/classes/{id}/teachers."""

import uuid

import pytest

pytestmark = pytest.mark.integration


def _admin(auth_headers):
    return auth_headers("admin@x.com", role="admin")


async def test_add_teacher_to_class_201(client, auth_headers, make_user, make_class):
    """MT1 — role=teacher → 201; appears in detail."""
    teacher = await make_user(email="mt1-t@x.com", role="teacher", full_name="T1")
    cls = await make_class(name="MT1")
    r = await client.post(
        f"/api/admin/classes/{cls['id']}/teachers",
        headers=_admin(auth_headers),
        json={"teacherId": teacher["id"]},
    )
    assert r.status_code == 201
    teachers = r.json()["data"]["class"]["teachers"]
    assert teacher["id"] in [t["id"] for t in teachers]


async def test_add_teacher_validates_role_400(
    client, auth_headers, make_user, make_class
):
    """MT2 — non-teacher user → 400."""
    student = await make_user(email="mt2-s@x.com", role="student")
    cls = await make_class(name="MT2")
    r = await client.post(
        f"/api/admin/classes/{cls['id']}/teachers",
        headers=_admin(auth_headers),
        json={"teacherId": student["id"]},
    )
    assert r.status_code == 400


async def test_add_teacher_nonexistent_user_404(client, auth_headers, make_class):
    """MT3"""
    cls = await make_class(name="MT3")
    r = await client.post(
        f"/api/admin/classes/{cls['id']}/teachers",
        headers=_admin(auth_headers),
        json={"teacherId": str(uuid.uuid4())},
    )
    assert r.status_code == 404


async def test_add_teacher_to_nonexistent_class_404(
    client, auth_headers, make_user
):
    """MT4"""
    teacher = await make_user(email="mt4-t@x.com", role="teacher")
    r = await client.post(
        f"/api/admin/classes/{uuid.uuid4()}/teachers",
        headers=_admin(auth_headers),
        json={"teacherId": teacher["id"]},
    )
    assert r.status_code == 404


async def test_add_duplicate_teacher_409(
    client, auth_headers, make_user, make_class
):
    """MT5"""
    teacher = await make_user(email="mt5-t@x.com", role="teacher")
    cls = await make_class(name="MT5", teacher_ids=[teacher["id"]])
    r = await client.post(
        f"/api/admin/classes/{cls['id']}/teachers",
        headers=_admin(auth_headers),
        json={"teacherId": teacher["id"]},
    )
    assert r.status_code == 409


async def test_add_multiple_teachers_to_one_class(
    client, auth_headers, make_user, make_class
):
    """MT6"""
    t1 = await make_user(email="mt6-t1@x.com", role="teacher")
    t2 = await make_user(email="mt6-t2@x.com", role="teacher")
    cls = await make_class(name="MT6")
    for t in (t1, t2):
        r = await client.post(
            f"/api/admin/classes/{cls['id']}/teachers",
            headers=_admin(auth_headers),
            json={"teacherId": t["id"]},
        )
        assert r.status_code == 201
    detail = r.json()["data"]["class"]
    assert detail["teacherCount"] == 2


async def test_remove_teacher_204(client, auth_headers, make_user, make_class):
    """MT7"""
    teacher = await make_user(email="mt7-t@x.com", role="teacher")
    cls = await make_class(name="MT7", teacher_ids=[teacher["id"]])
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}/teachers/{teacher['id']}",
        headers=_admin(auth_headers),
    )
    assert r.status_code == 204

    detail = await client.get(
        f"/api/admin/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert detail.json()["data"]["class"]["teachers"] == []


async def test_remove_teacher_not_member_404(
    client, auth_headers, make_user, make_class
):
    """MT8"""
    teacher = await make_user(email="mt8-t@x.com", role="teacher")
    cls = await make_class(name="MT8")
    r = await client.delete(
        f"/api/admin/classes/{cls['id']}/teachers/{teacher['id']}",
        headers=_admin(auth_headers),
    )
    assert r.status_code == 404


async def test_add_teacher_as_non_admin_403(
    client, auth_headers, make_user, make_class
):
    """MT9"""
    teacher = await make_user(email="mt9-t@x.com", role="teacher")
    cls = await make_class(name="MT9")
    r = await client.post(
        f"/api/admin/classes/{cls['id']}/teachers",
        headers=auth_headers("t@x.com", role="teacher"),
        json={"teacherId": teacher["id"]},
    )
    assert r.status_code == 403
