"""Response schemas for the FE-facing documentation endpoints (/api/docs)."""

from pydantic import BaseModel, Field


class DocInfo(BaseModel):
    """One served document (curated copy under docs/public/)."""

    slug: str = Field(..., description="Stable id used in GET /api/docs/{slug}.")
    title: str = Field(..., description="First markdown H1 of the doc (or the "
                                        "slug when the doc has no heading).")
    updatedAt: str = Field(..., description="`last-updated` from the doc "
                                            "frontmatter (YYYY-MM-DD), or the "
                                            "file mtime ISO date as fallback.")


class DocListData(BaseModel):
    items: list[DocInfo]


class DocListResponse(BaseModel):
    """GET /api/docs — list of all served docs (envelope per §10.10)."""

    status: int = 200
    data: DocListData


class DocContentData(BaseModel):
    slug: str
    title: str
    updatedAt: str
    content: str = Field(..., description="Raw markdown body, UTF-8.")


class DocContentResponse(BaseModel):
    """GET /api/docs/{slug} (JSON mode — without ?download=true)."""

    status: int = 200
    data: DocContentData
