from typing import Optional

from pydantic import BaseModel


class ChildView(BaseModel):
    """A student profile as seen by a parent."""

    id: str
    email: str
    fullName: str
    phone: Optional[str] = None
    createdAt: Optional[str] = None


class ChildrenListData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[ChildView]


class ChildrenListResponse(BaseModel):
    """Wrapped GET /api/parents/me/children response."""

    status: int = 200
    data: ChildrenListData
