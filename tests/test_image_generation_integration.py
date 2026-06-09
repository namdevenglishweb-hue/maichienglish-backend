"""Integration tests for AI image generation (DB-backed, mocked generator).

Gated by `@pytest.mark.integration` (needs MAICHI_TEST_DB=1 + a dedicated test
DB — db_pool DROPs all tables). AI + storage are mocked (no OpenRouter call,
no real upload). Covers API config-gate/RBAC/validation + job lifecycle.
"""
import pytest

pytestmark = pytest.mark.integration


class FakeImageGen:
    usage = {"images": 1, "input": 1, "output": 1}

    def __init__(self, acceptable=True):
        self._ok = acceptable

    async def generate_image(self, description, *, exam_context=None):
        return (b"PNG", "image/png")

    async def edit_image(self, source_url, description, *, exam_context=None):
        return (b"PNG", "image/png")

    async def verify_image(self, image_bytes, mime, description):
        return {"is_acceptable": self._ok, "reason": "ok" if self._ok else "bad number"}


class FakeStore:
    async def upload_bytes(self, bucket, content_type, data):
        return f"https://test/{bucket}/generated.png"


def _enable(monkeypatch):
    from config.settings import get_settings
    monkeypatch.setattr(get_settings(), "image_generation_enabled", True)


def _mock_ai(monkeypatch, acceptable=True):
    monkeypatch.setattr("services.ai.image_generator.get_image_generator",
                        lambda: FakeImageGen(acceptable))
    monkeypatch.setattr("services.storage_service.get_storage_service",
                        lambda: FakeStore())


# --------------------------------------------------------------------------
# API — config gate / RBAC / validation
# --------------------------------------------------------------------------

async def test_disabled_returns_409(db, client, auth_headers):
    # default IMAGE_GENERATION_ENABLED=false
    r = await client.post("/api/admin/image-generations",
                         json={"description": "a cat"},
                         headers=auth_headers("admin@x.com", role="admin"))
    assert r.status_code == 409


async def test_rbac_and_validation(db, client, auth_headers, monkeypatch):
    _enable(monkeypatch)
    # non-admin
    r = await client.post("/api/admin/image-generations", json={"description": "x"},
                         headers=auth_headers("s@x.com", role="student"))
    assert r.status_code == 403
    # no auth
    r = await client.post("/api/admin/image-generations", json={"description": "x"})
    assert r.status_code == 401
    # empty description
    r = await client.post("/api/admin/image-generations", json={"description": ""},
                         headers=auth_headers("admin@x.com", role="admin"))
    assert r.status_code == 422


async def test_post_accepts_and_creates_job(db, client, auth_headers, monkeypatch):
    _enable(monkeypatch)
    _mock_ai(monkeypatch)
    r = await client.post("/api/admin/image-generations",
                         json={"description": "a beach", "examContext": {"level": "KET", "skill": "reading"}},
                         headers=auth_headers("admin@x.com", role="admin"))
    assert r.status_code == 202
    job_id = r.json()["jobId"]
    r = await client.get(f"/api/admin/image-generations/{job_id}",
                        headers=auth_headers("admin@x.com", role="admin"))
    assert r.status_code == 200
    assert r.json()["mode"] == "generate"


# --------------------------------------------------------------------------
# Job runner e2e (service-level, real DB, mocked AI+storage)
# --------------------------------------------------------------------------

async def test_run_image_job_succeeds(db, monkeypatch):
    _mock_ai(monkeypatch, acceptable=True)
    from services.image_job_service import image_job_service, run_image_job
    job = await image_job_service.create_job(description="a beach")
    await run_image_job(job_id=job["jobId"], description="a beach")
    got = await image_job_service.get_job(job["jobId"])
    assert got["status"] == "succeeded"
    assert got["resultUrl"] == "https://test/images/generated.png"
    assert got["report"]["rounds"] == 1


async def test_run_image_job_edit_mode(db, monkeypatch):
    _mock_ai(monkeypatch, acceptable=True)
    from services.image_job_service import image_job_service, run_image_job
    job = await image_job_service.create_job(description="x", source_image_url="https://old.png")
    assert job["mode"] == "edit"
    await run_image_job(job_id=job["jobId"], description="x", source_image_url="https://old.png")
    got = await image_job_service.get_job(job["jobId"])
    assert got["status"] == "succeeded"


async def test_run_image_job_fails_keeps_reason(db, monkeypatch):
    _mock_ai(monkeypatch, acceptable=False)  # verify always rejects
    from services.image_job_service import image_job_service, run_image_job
    job = await image_job_service.create_job(description="clock")
    await run_image_job(job_id=job["jobId"], description="clock")
    got = await image_job_service.get_job(job["jobId"])
    assert got["status"] == "failed"
    assert got["resultUrl"] is None
    assert got["report"]["verifyReason"] == "bad number"


async def test_list_jobs_filter(db, monkeypatch):
    _mock_ai(monkeypatch, acceptable=True)
    from services.image_job_service import image_job_service, run_image_job
    job = await image_job_service.create_job(description="a")
    await run_image_job(job_id=job["jobId"], description="a")
    rows = await image_job_service.list_jobs(status="succeeded")
    assert any(r["jobId"] == job["jobId"] for r in rows)
