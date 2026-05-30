"""Integration tests for user endpoints — /api/users/me + /api/admin/users/*.

All tests hit a real Postgres via the fixtures in `conftest.py`.
Auto-skipped unless `MAICHI_TEST_DB=1` is set.
"""

import pytest

pytestmark = pytest.mark.integration


# ===========================================================================
# GET /api/users/me
# ===========================================================================


async def test_get_me_returns_profile_for_authenticated_user(client, make_user, auth_headers):
    user = await make_user(
        email="self@maichienglish.test",
        password="self-pw",
        full_name="Self Student",
        role="student",
        tier="basic",
    )

    r = await client.get(
        "/api/users/me",
        headers=auth_headers(user["email"], role="student", tier="basic"),
    )

    assert r.status_code == 200
    me = r.json()["data"]["user"]
    assert me["email"] == user["email"]
    assert me["fullName"] == "Self Student"
    assert me["role"] == "student"
    assert me["subscription"]["tier"] == "basic"


async def test_get_me_returns_401_without_token(client):
    r = await client.get("/api/users/me")
    assert r.status_code in (401, 403)


async def test_get_me_returns_404_when_jwt_user_no_longer_in_db(client, make_user, auth_headers):
    """JWT may outlive the user. /me must return 404, not crash."""
    from services.user_service import user_service

    user = await make_user(email="ghost@maichienglish.test", password="x")
    headers = auth_headers(user["email"])
    await user_service.delete_user(user["id"])

    r = await client.get("/api/users/me", headers=headers)
    assert r.status_code == 404


# ===========================================================================
# PUT /api/users/me
# ===========================================================================


async def test_put_me_updates_fullname_and_phone(client, make_user, auth_headers):
    user = await make_user(
        email="edit@maichienglish.test",
        password="x",
        full_name="Original Name",
        phone=None,
    )
    headers = auth_headers(user["email"])

    r = await client.put(
        "/api/users/me",
        headers=headers,
        json={"fullName": "Updated Name", "phone": "0912345678"},
    )

    assert r.status_code == 200
    me = r.json()["data"]["user"]
    assert me["fullName"] == "Updated Name"
    assert me["phone"] == "0912345678"

    # Re-read confirms persistence
    r2 = await client.get("/api/users/me", headers=headers)
    assert r2.json()["data"]["user"]["fullName"] == "Updated Name"


async def test_put_me_partial_update_only_provided_fields(client, make_user, auth_headers):
    """`exclude_unset` semantics: omitted fields stay unchanged."""
    user = await make_user(
        email="partial@maichienglish.test",
        password="x",
        full_name="Keep This Name",
        phone="0900000000",
    )
    headers = auth_headers(user["email"])

    r = await client.put(
        "/api/users/me",
        headers=headers,
        json={"phone": "0911111111"},
    )

    me = r.json()["data"]["user"]
    assert me["fullName"] == "Keep This Name"  # not touched
    assert me["phone"] == "0911111111"


async def test_put_me_empty_body_returns_400(client, make_user, auth_headers):
    user = await make_user(email="emptyput@maichienglish.test", password="x")
    headers = auth_headers(user["email"])

    r = await client.put("/api/users/me", headers=headers, json={})
    assert r.status_code == 400


# ===========================================================================
# Admin: GET /api/admin/users (paginated)
# ===========================================================================


async def test_admin_list_users_pagination_and_role_filter(client, make_user, auth_headers):
    admin = await make_user(
        email="lister@maichienglish.test", password="x", role="admin", tier="ultra"
    )
    await make_user(email="s1@maichienglish.test", password="x", role="student")
    await make_user(email="s2@maichienglish.test", password="x", role="student")
    await make_user(email="t1@maichienglish.test", password="x", role="teacher")

    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.get(
        "/api/admin/users?role=student&page=1&limit=10", headers=headers
    )
    assert r.status_code == 200
    data = r.json()["data"]
    emails = [u["email"] for u in data["items"]]
    assert "s1@maichienglish.test" in emails
    assert "s2@maichienglish.test" in emails
    assert "t1@maichienglish.test" not in emails
    assert data["pagination"]["total"] == 2


async def test_admin_list_users_rejects_non_admin(client, make_user, auth_headers):
    student = await make_user(
        email="snoop@maichienglish.test", password="x", role="student"
    )
    headers = auth_headers(student["email"], role="student")

    r = await client.get("/api/admin/users", headers=headers)
    assert r.status_code == 403


async def test_admin_list_users_caps_limit_at_100(client, make_user, auth_headers):
    admin = await make_user(
        email="capper@maichienglish.test", password="x", role="admin", tier="ultra"
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.get("/api/admin/users?limit=999", headers=headers)
    assert r.status_code == 422  # Pydantic le=100 rejects


# ===========================================================================
# Admin: POST /api/admin/users (create)
# ===========================================================================


async def test_admin_create_student_with_subscription(client, make_user, auth_headers):
    admin = await make_user(
        email="creator@maichienglish.test", password="x", role="admin", tier="ultra"
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "newstudent@maichienglish.test",
            "password": "initial-pw",
            "fullName": "New Student",
            "role": "student",
            "subscriptionTier": "basic",
        },
    )

    assert r.status_code == 201
    user = r.json()["data"]["user"]
    assert user["email"] == "newstudent@maichienglish.test"
    assert user["role"] == "student"
    assert user["subscription"]["tier"] == "basic"


async def test_admin_create_duplicate_email_returns_409(client, make_user, auth_headers):
    admin = await make_user(
        email="dup-admin@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "twice@maichienglish.test",
            "password": "pw1",
            "fullName": "First Insert",
        },
    )
    r2 = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "twice@maichienglish.test",
            "password": "pw2",
            "fullName": "Second Insert",
        },
    )
    assert r2.status_code == 409


async def test_admin_create_student_with_parent_id_links_parent(client, make_user, auth_headers):
    admin = await make_user(
        email="link-admin@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    parent = await make_user(
        email="parent1@maichienglish.test", password="x", role="parent"
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "child1@maichienglish.test",
            "password": "x",
            "fullName": "Child One",
            "role": "student",
            "parentId": parent["id"],
        },
    )

    assert r.status_code == 201
    assert r.json()["data"]["user"]["parentId"] == parent["id"]


async def test_admin_create_rejects_parent_id_on_non_student(client, make_user, auth_headers):
    """parent_id is silently dropped for non-students per service contract.
    The created teacher should have no parent link."""
    admin = await make_user(
        email="parent-strict@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    parent = await make_user(
        email="parent2@maichienglish.test", password="x", role="parent"
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "teach1@maichienglish.test",
            "password": "x",
            "fullName": "Teach One",
            "role": "teacher",
            "parentId": parent["id"],
        },
    )

    assert r.status_code == 201
    assert r.json()["data"]["user"]["parentId"] is None


# ===========================================================================
# Admin: DELETE /api/admin/users/{id}
# ===========================================================================


async def test_admin_delete_user_returns_204_and_removes(client, make_user, auth_headers):
    admin = await make_user(
        email="killer@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    victim = await make_user(email="bye@maichienglish.test", password="x")
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.delete(f"/api/admin/users/{victim['id']}", headers=headers)
    assert r.status_code == 204

    # Victim's token now points at a non-existent user → /me returns 404
    me = await client.get(
        "/api/users/me", headers=auth_headers(victim["email"])
    )
    assert me.status_code == 404


async def test_admin_delete_nonexistent_user_returns_404(client, make_user, auth_headers):
    admin = await make_user(
        email="missing-killer@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.delete(
        "/api/admin/users/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert r.status_code == 404


# ===========================================================================
# Admin: POST /api/admin/users/{id}/reset-password
# ===========================================================================


async def test_admin_reset_password_changes_login(client, make_user, auth_headers):
    admin = await make_user(
        email="resetter@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    target = await make_user(
        email="reset-me@maichienglish.test", password="old-pw"
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    r = await client.post(
        f"/api/admin/users/{target['id']}/reset-password",
        headers=headers,
        json={"newPassword": "freshly-reset-pw"},
    )
    assert r.status_code == 204

    # Old pw rejected, new pw works
    bad = await client.post(
        "/api/auth/login",
        json={"email": target["email"], "password": "old-pw"},
    )
    ok = await client.post(
        "/api/auth/login",
        json={"email": target["email"], "password": "freshly-reset-pw"},
    )
    assert bad.status_code == 401
    assert ok.status_code == 200


# ===========================================================================
# Admin: PUT /api/admin/users/{student_id}/parent
# ===========================================================================


async def test_admin_link_parent_then_unlink(client, make_user, auth_headers):
    admin = await make_user(
        email="linker@maichienglish.test",
        password="x",
        role="admin",
        tier="ultra",
    )
    parent = await make_user(
        email="big-parent@maichienglish.test", password="x", role="parent"
    )
    student = await make_user(
        email="orphan@maichienglish.test", password="x", role="student"
    )
    headers = auth_headers(admin["email"], role="admin", tier="ultra")

    # Link
    link = await client.put(
        f"/api/admin/users/{student['id']}/parent",
        headers=headers,
        json={"parentId": parent["id"]},
    )
    assert link.status_code == 200
    assert link.json()["data"]["user"]["parentId"] == parent["id"]

    # Unlink
    unlink = await client.put(
        f"/api/admin/users/{student['id']}/parent",
        headers=headers,
        json={"parentId": None},
    )
    assert unlink.status_code == 200
    assert unlink.json()["data"]["user"]["parentId"] is None
