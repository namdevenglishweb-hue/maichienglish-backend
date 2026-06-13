"""FE-facing documentation endpoints — GET /api/docs (+ /{slug}).

Serves the curated markdown set committed under docs/public/. Security model:
a HARD ALLOWLIST keyed by slug — the filesystem is never scanned and the slug
is never used to build a path, so traversal (../, absolute paths, weird
encodings) can only ever produce a 404. Defense-in-depth: the resolved path
is asserted to live inside docs/public anyway.

No auth for now (decided 2026-06-12; FE consumes these directly). To gate
later, add the dependency in ONE place:
    router = APIRouter(..., dependencies=[Depends(require_admin)])
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response, status

from .schemas import (
    DocContentData,
    DocContentResponse,
    DocInfo,
    DocListData,
    DocListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/docs",
    tags=["Docs · FE-facing documentation"],
    # auth deliberately absent for now — see module docstring before adding.
)

_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "docs" / "public"

# HARD ALLOWLIST — adding a doc to the API is an explicit code change here
# (plus committing the file under docs/public/), never a filesystem effect.
_PUBLIC_SLUGS = [
    # AI features (round 1)
    "exam-ai-generation-design", "exam-ai-generation-frontend",
    "exam-image-generation-design", "exam-image-generation-frontend",
    "exam-gen-v3-spec-mode-design", "exam-gen-v3-spec-mode-frontend",
    "exam-part-presets-design", "exam-part-presets-frontend",
    # remaining per-feature design+frontend (round 2, 2026-06-12)
    "attempt-highlights-design", "attempt-highlights-frontend",
    "attempt-lifecycle-design", "attempt-lifecycle-frontend",
    "class-management-design", "class-management-frontend",
    "email-design", "email-frontend",
    "exam-mode-design", "exam-mode-frontend",
    "exam-publish-lock-design", "exam-publish-lock-frontend",
    "session-management-design", "session-management-frontend",
    "teacher-grading-design", "teacher-grading-frontend",
    "writing-speaking-design", "writing-speaking-frontend",
]
PUBLIC_DOCS: dict[str, str] = {slug: f"{slug}.md" for slug in _PUBLIC_SLUGS}

_FRONTMATTER_UPDATED = re.compile(r"^last-updated:\s*(\S+)", re.MULTILINE)
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _load_doc(slug: str) -> tuple[Path, str]:
    """Resolve a slug via the allowlist (404 on anything else) and read it."""
    filename = PUBLIC_DOCS.get(slug)
    if filename is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown doc {slug!r}")
    path = (_PUBLIC_DIR / filename).resolve()
    if _PUBLIC_DIR.resolve() not in path.parents:  # defense-in-depth
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown doc {slug!r}")
    try:
        return path, path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("public doc %s missing on disk", slug)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Doc {slug!r} unavailable")


def _doc_info(slug: str, path: Path, content: str) -> DocInfo:
    m = _H1.search(content)
    title = m.group(1).strip() if m else slug
    fm = _FRONTMATTER_UPDATED.search(content)
    from datetime import datetime, timezone
    updated = fm.group(1) if fm else datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    return DocInfo(slug=slug, title=title, updatedAt=updated)


@router.get("", response_model=DocListResponse, status_code=status.HTTP_200_OK)
async def list_docs():
    """List every served document (slug + title + last-updated).

    The set is the hard allowlist above — curated copies committed under
    docs/public/, NOT the full local docs tree.
    """
    items = []
    for slug in PUBLIC_DOCS:
        path, content = _load_doc(slug)
        items.append(_doc_info(slug, path, content))
    return DocListResponse(data=DocListData(items=items))


@router.get("/{slug}", response_model=DocContentResponse,
            status_code=status.HTTP_200_OK)
async def get_doc(slug: str, download: bool = Query(
        default=False,
        description="true → raw text/markdown attachment (Content-Disposition) "
                    "instead of the JSON envelope.")):
    """Return one document's markdown.

    Default: JSON envelope (DocContentResponse). With `?download=true` the
    response is the raw markdown file as an attachment — the JSON
    response_model documents the default mode only.
    Unknown slug / traversal attempts → 404 (allowlist; the slug never
    touches the filesystem path).
    """
    path, content = _load_doc(slug)
    info = _doc_info(slug, path, content)
    if download:
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{PUBLIC_DOCS[slug]}"'},
        )
    return DocContentResponse(data=DocContentData(
        slug=slug, title=info.title, updatedAt=info.updatedAt, content=content))
