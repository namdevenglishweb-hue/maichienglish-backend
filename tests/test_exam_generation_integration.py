"""Integration tests for AI exam generation (DB-backed, mocked AI).

Gated by `@pytest.mark.integration` (auto-skipped unless MAICHI_TEST_DB=1 +
a dedicated test DB — the db_pool fixture DROPS + recreates all tables, so
NEVER point it at a real database). Mirrors the manual dev verification that
already passed; these run in CI (Docker Postgres).

AI is faked (no API key). Service-layer tests inject the fake directly;
API-layer tests monkeypatch the generator factory so the BackgroundTasks
runner picks it up.
"""
import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# Fake generator — builds valid output from the source section in the payload,
# so it works for any seed shape. Forces nothing (the service re-imposes
# invariants); just tweaks content + emits new media meta.
# --------------------------------------------------------------------------

class FakeGen:
    usage = {"input": 10, "output": 20}

    async def generate_section(self, payload, *, k):
        s = payload["section"]
        mats = []
        for m in s["materials"]:
            t = m["type"]
            if t == "text":
                mats.append({"type": "text", "content": m.get("content", "")})
            elif t == "audio":
                mats.append({"type": "audio", "meta": {"transcript": "NEW script"}})
            elif t == "image":
                mats.append({"type": "image", "meta": {"description": "NEW desc"}})
        qs = []
        for q in s["questions"]:
            qd = dict(q["question_data"])
            if q["question_type"] in ("multiple_choice", "matching"):
                qd["stem"] = "NEW " + str(qd.get("stem", ""))
                qs.append({"question_type": q["question_type"], "question_data": qd,
                           "answer_justification": "ev"})
            else:
                qs.append({"question_type": q["question_type"], "question_data": qd})
        return {"part_label": s.get("part_label"), "instructions": s.get("instructions"),
                "materials": mats, "questions": qs}

    async def verify_section(self, section, payload, *, k):
        return {"is_acceptable": True, "issues": []}


def _reading_sections():
    """One text-MC section + one audio+gap fill_blank section (audio has meta)."""
    return [
        {"type": "multiple_choice",
         "materials": [{"type": "text", "content": "A passage about football."}],
         "questions": [{"question_type": "multiple_choice",
                        "question_data": {"stem": "Sport?", "options": [{"text": "Foot"}, {"text": "Tennis"}],
                                          "correct_index": 0}}]},
        {"type": "fill_blank",
         "materials": [
             {"type": "audio", "url": "https://example.com/a.mp3",
              "meta": {"transcript": "old transcript", "pendingReplacement": False}},
             {"type": "text", "content": "Name {{gap:1}} Age {{gap:2}}"}],
         "questions": [{"question_type": "fill_blank", "question_data": {"correct_answers": ["John"]}},
                       {"question_type": "fill_blank", "question_data": {"correct_answers": ["10"]}}]},
    ]


# --------------------------------------------------------------------------
# Service layer (real DB, fake AI injected directly)
# --------------------------------------------------------------------------

async def test_mode1_generate_persists_with_provenance(db, make_exam):
    from services.exam_generation_service import exam_generation_service
    from services.exam_service import exam_service

    src = await make_exam(skill="reading", sections=_reading_sections())
    report = await exam_generation_service.generate_similar_exam(
        src["id"], 3, generator=FakeGen(), rounds=1)

    assert report["sections_ok"] == 2
    assert report["media_todos"] == [
        {"section_position": 2, "material_index": 0, "media_type": "audio"}]
    assert set(report["self_review"]) == {"1", "2"}

    new = await exam_service.get_exam(report["new_exam_id"])
    assert new["is_published"] is False
    assert new["generated_from_exam_id"] == src["id"]
    assert new["generation_meta"]["model"]


async def test_mode1_abort_creates_no_exam(db, db_pool, make_exam):
    from services.exam_generation_service import (
        GenerationAborted, exam_generation_service)

    class BadGen(FakeGen):
        async def verify_section(self, section, payload, *, k):
            return {"is_acceptable": False,
                    "issues": [{"severity": "critical", "problem": "wrong"}]}

    src = await make_exam(skill="reading", sections=_reading_sections())
    before = await db_pool.fetchval("SELECT count(*) FROM exams WHERE deleted_at IS NULL")
    with pytest.raises(GenerationAborted):
        await exam_generation_service.generate_similar_exam(
            src["id"], 2, generator=BadGen(), rounds=1)
    after = await db_pool.fetchval("SELECT count(*) FROM exams WHERE deleted_at IS NULL")
    assert before == after  # all-or-nothing: nothing persisted


async def test_precondition_missing_transcript_rejected(db, make_exam):
    from services.exam_generation_service import exam_generation_service
    from services.exceptions import ValidationError

    src = await make_exam(skill="reading", sections=[
        {"type": "fill_blank",
         "materials": [{"type": "audio", "url": "https://x/b.mp3"}],  # no meta.transcript
         "questions": [{"question_type": "fill_blank",
                        "question_data": {"correct_answers": ["x"]}}]}])
    with pytest.raises(ValidationError):
        await exam_generation_service.precheck_exam_source(src["id"])


async def test_assemble_creates_draft_with_provenance(db, make_exam):
    from services.exam_generation_service import exam_generation_service

    src = await make_exam(skill="reading", sections=_reading_sections())
    prev = await exam_generation_service.generate_sections_preview(
        src["id"], 2, generator=FakeGen())
    assert all(e["status"] == "ok" for e in prev["sections"])
    secs = [e["section"] for e in prev["sections"]]

    result = await exam_generation_service.assemble_generated_exam(
        src["id"], secs, title="Assembled", k=2)
    assert result["exam"]["is_published"] is False
    assert result["exam"]["generated_from_exam_id"] == src["id"]
    assert result["warning"] is None  # 2 == source's 2 sections


# --------------------------------------------------------------------------
# Section-type prompts API (admin CRUD + RBAC)
# --------------------------------------------------------------------------

async def test_section_type_prompt_crud_admin(db, client, auth_headers):
    h = auth_headers("admin@x.com", role="admin")
    r = await client.put("/api/admin/section-type-prompts/form_completion",
                         json={"additionalPrompt": "Build a KET form."}, headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["additionalPrompt"] == "Build a KET form."

    r = await client.get("/api/admin/section-type-prompts", headers=h)
    types = {p["type"] for p in r.json()["data"]["items"]}
    assert "form_completion" in types

    r = await client.put("/api/admin/section-type-prompts/not_a_type",
                         json={"additionalPrompt": "x"}, headers=h)
    assert r.status_code == 422  # invalid type

    r = await client.delete("/api/admin/section-type-prompts/form_completion", headers=h)
    assert r.status_code == 204
    r = await client.delete("/api/admin/section-type-prompts/form_completion", headers=h)
    assert r.status_code == 404  # already gone


async def test_section_type_prompt_rbac(db, client, auth_headers):
    r = await client.get("/api/admin/section-type-prompts",
                        headers=auth_headers("s@x.com", role="student"))
    assert r.status_code == 403
    r = await client.get("/api/admin/section-type-prompts")  # no auth
    assert r.status_code == 401


# --------------------------------------------------------------------------
# Generation API — validation / RBAC (synchronous, before background)
# --------------------------------------------------------------------------

async def test_generate_exam_validation(db, client, auth_headers, make_exam):
    admin = auth_headers("admin@x.com", role="admin")

    r = await client.post("/api/admin/exam-generations",
                        json={"sourceExamId": "00000000-0000-0000-0000-000000000000", "k": 3},
                        headers=admin)
    assert r.status_code == 404  # source missing

    r = await client.post("/api/admin/exam-generations",
                        json={"sourceExamId": "x", "k": 0}, headers=admin)
    assert r.status_code == 422  # k out of range (pydantic)

    bad = await make_exam(skill="reading", sections=[
        {"type": "fill_blank",
         "materials": [{"type": "audio", "url": "https://x/b.mp3"}],
         "questions": [{"question_type": "fill_blank",
                        "question_data": {"correct_answers": ["x"]}}]}])
    r = await client.post("/api/admin/exam-generations",
                        json={"sourceExamId": bad["id"], "k": 3}, headers=admin)
    assert r.status_code == 400  # precondition: audio missing transcript

    src = await make_exam(skill="reading", sections=_reading_sections())
    r = await client.post("/api/admin/exam-generations",
                        json={"sourceExamId": src["id"], "k": 3},
                        headers=auth_headers("t@x.com", role="teacher"))
    assert r.status_code == 403  # admin-only


async def test_generate_exam_accepts_and_creates_job(
    db, client, auth_headers, make_exam, monkeypatch):
    monkeypatch.setattr(
        "services.exam_generation_service.get_ai_generator", lambda: FakeGen())
    src = await make_exam(skill="reading", sections=_reading_sections())
    r = await client.post("/api/admin/exam-generations",
                        json={"sourceExamId": src["id"], "k": 3},
                        headers=auth_headers("admin@x.com", role="admin"))
    assert r.status_code == 202
    job_id = r.json()["jobId"]

    # Job row exists and is in a valid lifecycle state.
    r = await client.get(f"/api/admin/exam-generations/{job_id}",
                       headers=auth_headers("admin@x.com", role="admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "exam"
    assert body["status"] in ("pending", "running", "succeeded")


# --------------------------------------------------------------------------
# Assembled-exam API (sync Save)
# --------------------------------------------------------------------------

async def test_assembled_exam_endpoint(db, client, auth_headers, make_exam):
    from services.exam_generation_service import exam_generation_service

    src = await make_exam(skill="reading", sections=_reading_sections())
    prev = await exam_generation_service.generate_sections_preview(
        src["id"], 2, generator=FakeGen())
    secs = [e["section"] for e in prev["sections"]]
    admin = auth_headers("admin@x.com", role="admin")

    r = await client.post("/api/admin/exam-generations/assembled-exam",
                        json={"sourceExamId": src["id"], "sections": secs, "k": 2},
                        headers=admin)
    assert r.status_code == 201
    assert r.json()["data"]["exam"]["isPublished"] is False

    # Bad section shape → 400 (BE re-validates).
    r = await client.post("/api/admin/exam-generations/assembled-exam",
                        json={"sourceExamId": src["id"],
                              "sections": [{"type": "multiple_choice", "materials": [],
                                            "questions": [{"question_type": "multiple_choice",
                                                           "question_data": {"options": []}}]}]},
                        headers=admin)
    assert r.status_code == 400

    r = await client.post("/api/admin/exam-generations/assembled-exam",
                        json={"sourceExamId": src["id"], "sections": secs},
                        headers=auth_headers("s@x.com", role="student"))
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Exam read — provenance + meta gating + material.meta strip (HTTP)
# --------------------------------------------------------------------------

async def test_exam_read_provenance_and_meta_gating(db, client, auth_headers, make_exam):
    from services.exam_generation_service import exam_generation_service

    src = await make_exam(skill="reading", sections=_reading_sections())
    report = await exam_generation_service.generate_similar_exam(
        src["id"], 3, generator=FakeGen(), rounds=1)
    # Publish so a student can read it.
    from services.exam_service import exam_service
    await exam_service.publish_exam(report["new_exam_id"])
    new_id = report["new_exam_id"]

    # Admin: sees generationMeta + generatedFromExamId + material.meta.
    r = await client.get(f"/api/exams/{new_id}?include=sections",
                       headers=auth_headers("admin@x.com", role="admin"))
    body = r.json()["data"]["exam"]
    assert body["generatedFromExamId"] == src["id"]
    assert body["generationMeta"]["model"]
    audio = next(m for s in body["sections"] for m in s["materials"] if m["type"] == "audio")
    assert "meta" in audio  # admin keeps transcript

    # Student: badge id yes, generationMeta None, material.meta stripped.
    r = await client.get(f"/api/exams/{new_id}?include=sections",
                       headers=auth_headers("s@x.com", role="student"))
    body = r.json()["data"]["exam"]
    assert body["generatedFromExamId"] == src["id"]
    assert body["generationMeta"] is None
    audio = next(m for s in body["sections"] for m in s["materials"] if m["type"] == "audio")
    assert "meta" not in audio  # stripped — transcript would leak the listening answer
