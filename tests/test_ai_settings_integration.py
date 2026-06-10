"""API-level tests for runtime-editable AI generation settings (migration 0022).

Gated integration tests (DB-backed). The contract the FE relies on: read the
effective config, update any subset without redeploy, clear a field back to the
env default, RBAC + provider validation.
"""
import pytest

pytestmark = pytest.mark.integration

_URL = "/api/admin/ai-settings"


async def test_defaults_to_env_when_unset(db, client, auth_headers):
    r = await client.get(_URL, headers=auth_headers("a@x.com", role="admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["effective"]["provider"]            # some env default
    assert body["effective"]["selfReviewRounds"] is not None
    assert body["stored"]["provider"] is None        # nothing stored yet


async def test_update_and_resolve(db, client, auth_headers):
    admin = auth_headers("a@x.com", role="admin")
    r = await client.put(_URL, json={"selfReviewRounds": 0, "model": "groq/test-x"},
                         headers=admin)
    assert r.status_code == 200
    eff = r.json()["effective"]
    assert eff["selfReviewRounds"] == 0 and eff["model"] == "groq/test-x"

    # persisted: GET reflects it
    r = await client.get(_URL, headers=admin)
    assert r.json()["stored"]["selfReviewRounds"] == 0
    assert r.json()["effective"]["model"] == "groq/test-x"


async def test_clear_field_reverts_to_default(db, client, auth_headers):
    admin = auth_headers("a@x.com", role="admin")
    await client.put(_URL, json={"model": "groq/temp"}, headers=admin)
    r = await client.put(_URL, json={"model": None}, headers=admin)  # explicit null clears
    assert r.status_code == 200
    assert r.json()["stored"]["model"] is None        # back to env default


async def test_provider_validation_and_rbac(db, client, auth_headers):
    admin = auth_headers("a@x.com", role="admin")
    r = await client.put(_URL, json={"provider": "bogus"}, headers=admin)
    assert r.status_code == 400
    r = await client.put(_URL, json={"model": "x"},
                         headers=auth_headers("s@x.com", role="student"))
    assert r.status_code == 403
    r = await client.get(_URL)  # no auth
    assert r.status_code == 401
