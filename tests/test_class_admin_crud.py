"""Admin class CRUD — CL6-CL13. Hits /api/admin/classes via the client."""

import pytest

pytestmark = pytest.mark.integration

ADMIN = {"email": "admin@maichienglish.com", "role": "admin"}


def _admin(auth_headers):
    return auth_headers(ADMIN["email"], role="admin")


async def test_create_class_returns_201_with_zero_counts(client, auth_headers):
    """CL6"""
    r = await client.post(
        "/api/admin/classes",
        headers=_admin(auth_headers),
        json={"name": "KET Morning", "description": "8am batch"},
    )
    assert r.status_code == 201
    c = r.json()["data"]["class"]
    assert c["name"] == "KET Morning"
    assert c["teacherCount"] == 0
    assert c["studentCount"] == 0
    assert "id" in c and "createdAt" in c


async def test_create_class_requires_name(client, auth_headers):
    """CL7 — empty name → 422 from Pydantic."""
    r = await client.post(
        "/api/admin/classes", headers=_admin(auth_headers), json={"name": ""}
    )
    assert r.status_code == 422


async def test_list_classes_returns_member_counts(
    client, auth_headers, make_user, make_class
):
    """CL8"""
    teacher = await make_user(email="cl8-t@x.com", role="teacher")
    s1 = await make_user(email="cl8-s1@x.com", role="student")
    s2 = await make_user(email="cl8-s2@x.com", role="student")
    await make_class(
        name="Counted",
        teacher_ids=[teacher["id"]],
        student_ids=[s1["id"], s2["id"]],
    )

    r = await client.get("/api/admin/classes", headers=_admin(auth_headers))
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    target = next(c for c in items if c["name"] == "Counted")
    assert target["teacherCount"] == 1
    assert target["studentCount"] == 2


async def test_get_class_detail_includes_teachers_and_students(
    client, auth_headers, make_user, make_class
):
    """CL9"""
    teacher = await make_user(email="cl9-t@x.com", role="teacher", full_name="Tee")
    student = await make_user(email="cl9-s@x.com", role="student", full_name="Stu")
    cls = await make_class(
        name="Detailed", teacher_ids=[teacher["id"]], student_ids=[student["id"]]
    )

    r = await client.get(
        f"/api/admin/classes/{cls['id']}", headers=_admin(auth_headers)
    )
    assert r.status_code == 200
    data = r.json()["data"]["class"]
    assert [t["fullName"] for t in data["teachers"]] == ["Tee"]
    assert [s["email"] for s in data["students"]] == ["cl9-s@x.com"]


async def test_get_class_404_when_not_found(client, auth_headers):
    """CL10"""
    import uuid

    r = await client.get(
        f"/api/admin/classes/{uuid.uuid4()}", headers=_admin(auth_headers)
    )
    assert r.status_code == 404


async def test_update_class_name_description(
    client, auth_headers, make_class, db_pool
):
    """CL11 — PATCH updates fields AND bumps updated_at."""
    import uuid

    cls = await make_class(name="Old", description="old desc")
    async with db_pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT updated_at FROM public.classes WHERE id=$1", uuid.UUID(cls["id"])
        )

    r = await client.patch(
        f"/api/admin/classes/{cls['id']}",
        headers=_admin(auth_headers),
        json={"name": "New", "description": "new desc"},
    )
    assert r.status_code == 200
    data = r.json()["data"]["class"]
    assert data["name"] == "New"
    assert data["description"] == "new desc"

    async with db_pool.acquire() as conn:
        after = await conn.fetchval(
            "SELECT updated_at FROM public.classes WHERE id=$1", uuid.UUID(cls["id"])
        )
    assert after > before


async def test_create_class_as_non_admin_403(client, auth_headers):
    """CL12"""
    for role in ("teacher", "student"):
        r = await client.post(
            "/api/admin/classes",
            headers=auth_headers(f"{role}@x.com", role=role),
            json={"name": "X"},
        )
        assert r.status_code == 403


async def test_class_endpoints_require_auth_401(client):
    """CL13"""
    r = await client.get("/api/admin/classes")
    assert r.status_code in (401, 403)
