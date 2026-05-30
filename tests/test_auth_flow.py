"""Integration tests for /api/auth/* — login, refresh, verify, password reset.

All tests hit a real Postgres via the fixtures in `conftest.py`. They
run only when `MAICHI_TEST_DB=1` is set (CI integration job or local
Docker setup).
"""

import pytest

pytestmark = pytest.mark.integration


# ===========================================================================
# POST /api/auth/login
# ===========================================================================


async def test_login_returns_tokens_for_valid_credentials(client, make_user):
    user = await make_user(
        email="alice@maichienglish.test",
        password="alice-secret-pw",
        role="admin",
        tier="ultra",
    )

    r = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "alice-secret-pw"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == 200
    assert body["data"]["user"]["email"] == user["email"]
    assert body["data"]["user"]["role"] == "admin"
    assert body["data"]["user"]["subscription"]["tier"] == "ultra"
    assert body["data"]["token"]["accessToken"]
    assert body["data"]["token"]["refreshToken"]
    assert body["data"]["token"]["expiresIn"] > 0


async def test_login_wrong_password_returns_401_vietnamese_message(client, make_user):
    await make_user(email="bob@maichienglish.test", password="correct-pw")

    r = await client.post(
        "/api/auth/login",
        json={"email": "bob@maichienglish.test", "password": "wrong-pw"},
    )

    assert r.status_code == 401
    assert "không đúng" in r.json()["detail"]


async def test_login_unknown_email_returns_same_401(client):
    """Anti-enumeration: unknown email should be indistinguishable from
    wrong password (same status code + same message)."""
    r = await client.post(
        "/api/auth/login",
        json={"email": "ghost@maichienglish.test", "password": "anything"},
    )

    assert r.status_code == 401
    assert "không đúng" in r.json()["detail"]


async def test_login_normalizes_email_lowercase_and_strips_plus(client, make_user):
    """`_normalize_email` lowercases + strips `+alias` — login should
    accept any equivalent form."""
    await make_user(email="carol@maichienglish.test", password="carol-pw")

    r = await client.post(
        "/api/auth/login",
        json={"email": "Carol+work@MAICHIenglish.test", "password": "carol-pw"},
    )

    assert r.status_code == 200


async def test_login_missing_required_field_returns_422(client):
    r = await client.post("/api/auth/login", json={"email": "x@y.com"})
    assert r.status_code == 422  # Pydantic missing 'password'


# ===========================================================================
# POST /api/auth/refresh
# ===========================================================================


async def test_refresh_with_valid_refresh_token_issues_new_pair(client, make_user):
    user = await make_user(email="dave@maichienglish.test", password="dave-pw")

    login = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "dave-pw"},
    )
    refresh_token = login.json()["data"]["token"]["refreshToken"]

    r = await client.post(
        "/api/auth/refresh",
        json={"refreshToken": refresh_token},
    )

    assert r.status_code == 200
    new_pair = r.json()["data"]["token"]
    assert new_pair["accessToken"] and new_pair["accessToken"] != refresh_token
    assert new_pair["refreshToken"]


async def test_refresh_rejects_access_token_as_refresh(client, make_user):
    """Passing an access token to /refresh must fail — token type confusion
    is a common JWT bug, must be rejected at the boundary."""
    user = await make_user(email="eve@maichienglish.test", password="eve-pw")
    login = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "eve-pw"},
    )
    access_token = login.json()["data"]["token"]["accessToken"]

    r = await client.post(
        "/api/auth/refresh",
        json={"refreshToken": access_token},
    )

    assert r.status_code == 401
    assert "Invalid" in r.json()["detail"] or "expired" in r.json()["detail"]


async def test_refresh_rejects_garbage_token(client):
    r = await client.post(
        "/api/auth/refresh",
        json={"refreshToken": "not-a-jwt"},
    )
    assert r.status_code == 401


async def test_refresh_rejects_token_for_deleted_user(client, make_user):
    """Refresh token may outlive the user — backend must re-check existence."""
    from services.user_service import user_service

    user = await make_user(email="frank@maichienglish.test", password="frank-pw")
    login = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "frank-pw"},
    )
    refresh_token = login.json()["data"]["token"]["refreshToken"]

    await user_service.delete_user(user["id"])

    r = await client.post(
        "/api/auth/refresh",
        json={"refreshToken": refresh_token},
    )
    assert r.status_code == 401


# ===========================================================================
# POST /api/auth/verify
# ===========================================================================


async def test_verify_returns_claims_for_valid_access_token(client, make_user, auth_headers):
    user = await make_user(
        email="gina@maichienglish.test",
        password="gina-pw",
        role="teacher",
        tier="pro",
    )

    r = await client.post(
        "/api/auth/verify",
        headers=auth_headers(user["email"], role="teacher", tier="pro"),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["data"]["valid"] is True
    assert body["data"]["user"]["email"] == user["email"]
    assert body["data"]["user"]["role"] == "teacher"
    assert body["data"]["user"]["tier"] == "pro"


async def test_verify_rejects_missing_bearer(client):
    r = await client.post("/api/auth/verify")
    assert r.status_code in (401, 403)  # HTTPBearer returns 403, FastAPI may map


async def test_verify_rejects_garbage_token(client):
    r = await client.post(
        "/api/auth/verify",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert r.status_code == 401


# ===========================================================================
# POST /api/auth/password/request-code  +  POST /api/auth/password/reset
# ===========================================================================


async def test_password_reset_full_flow(client, make_user):
    """Happy path: request code → reset with code → login with new password."""
    user = await make_user(email="harry@maichienglish.test", password="old-pw")

    code_resp = await client.post(
        "/api/auth/password/request-code",
        json={"email": user["email"]},
    )
    assert code_resp.status_code == 200
    code = code_resp.json()["data"]["devCode"]
    assert code and len(code) == 6 and code.isdigit()

    reset_resp = await client.post(
        "/api/auth/password/reset",
        json={
            "email": user["email"],
            "code": code,
            "newPassword": "brand-new-pw",
        },
    )
    assert reset_resp.status_code == 200

    # Old password rejected
    bad = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "old-pw"},
    )
    assert bad.status_code == 401

    # New password accepted
    ok = await client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "brand-new-pw"},
    )
    assert ok.status_code == 200


async def test_password_request_code_for_unknown_email_silent_200(client):
    """Anti-enumeration: unknown email must return 200 with devCode=None
    (no leak of whether the email exists)."""
    r = await client.post(
        "/api/auth/password/request-code",
        json={"email": "no-such-user@maichienglish.test"},
    )

    assert r.status_code == 200
    assert r.json()["data"]["devCode"] is None


async def test_password_reset_rejects_wrong_code(client, make_user):
    user = await make_user(email="ivy@maichienglish.test", password="ivy-pw")
    await client.post(
        "/api/auth/password/request-code", json={"email": user["email"]}
    )

    r = await client.post(
        "/api/auth/password/reset",
        json={
            "email": user["email"],
            "code": "000000",  # almost certainly not the real code
            "newPassword": "should-not-apply",
        },
    )

    assert r.status_code == 400
    assert "Invalid" in r.json()["detail"] or "expired" in r.json()["detail"]


async def test_password_reset_invalidates_code_after_use(client, make_user):
    """Each code is single-use: re-submitting with the same code must fail."""
    user = await make_user(email="jane@maichienglish.test", password="jane-pw")

    code_resp = await client.post(
        "/api/auth/password/request-code", json={"email": user["email"]}
    )
    code = code_resp.json()["data"]["devCode"]

    first = await client.post(
        "/api/auth/password/reset",
        json={
            "email": user["email"],
            "code": code,
            "newPassword": "first-reset-pw",
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/auth/password/reset",
        json={
            "email": user["email"],
            "code": code,
            "newPassword": "second-reset-pw",
        },
    )
    assert second.status_code == 400


async def test_requesting_new_code_invalidates_previous_one(client, make_user):
    """Second /request-code marks the first code used — old code can no
    longer reset."""
    user = await make_user(email="kate@maichienglish.test", password="kate-pw")

    first = await client.post(
        "/api/auth/password/request-code", json={"email": user["email"]}
    )
    first_code = first.json()["data"]["devCode"]

    await client.post(
        "/api/auth/password/request-code", json={"email": user["email"]}
    )

    r = await client.post(
        "/api/auth/password/reset",
        json={
            "email": user["email"],
            "code": first_code,
            "newPassword": "should-fail",
        },
    )
    assert r.status_code == 400
