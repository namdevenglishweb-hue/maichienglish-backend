"""High-level tests for the FE-facing docs API (/api/docs).

No DB needed — the handlers are pure file reads, so they're called directly
(the app lifespan would otherwise demand a live pool). Covers: list shape,
valid slug content, allowlist rejection (unknown slug + traversal payloads),
and the download header.
"""
import pytest
from fastapi import HTTPException

from api.public_docs.routes import PUBLIC_DOCS, get_doc, list_docs


async def test_list_returns_every_allowlisted_doc():
    resp = await list_docs()
    assert resp.status == 200
    items = {d.slug: d for d in resp.data.items}
    assert set(items) == set(PUBLIC_DOCS)
    for d in items.values():
        assert d.title and d.updatedAt  # H1 + frontmatter/mtime resolved


async def test_get_doc_returns_markdown_content():
    resp = await get_doc("exam-gen-v3-spec-mode-frontend", download=False)
    assert resp.status == 200
    assert resp.data.slug == "exam-gen-v3-spec-mode-frontend"
    assert "promptVersion" in resp.data.content  # real doc body, not a stub


@pytest.mark.parametrize("bad_slug", [
    "nope",
    "../../.env",
    "..%2F..%2F.env",
    "exam-ai-generation-testcases",   # exists locally but NOT allowlisted
    "....//....//main.py",
])
async def test_get_doc_rejects_unknown_and_traversal_slugs(bad_slug):
    with pytest.raises(HTTPException) as e:
        await get_doc(bad_slug, download=False)
    assert e.value.status_code == 404


async def test_download_sets_attachment_headers():
    resp = await get_doc("exam-ai-generation-frontend", download=True)
    assert resp.media_type.startswith("text/markdown")
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment;") and "exam-ai-generation-frontend.md" in cd
