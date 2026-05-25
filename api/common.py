"""Shared request schemas used across multiple api/<domain>/ packages.

Kept in `api/` (not in a per-domain package) to avoid circular imports
between domain packages that both want the same shape — e.g. batch-delete
takes the same body in `api/sections/routes.py` and
`api/questions/routes.py`.
"""
from pydantic import BaseModel, Field

MAX_BATCH_SIZE = 100


class BatchDeleteRequest(BaseModel):
    """Body for `POST /api/sections/batch-delete` and `POST /api/questions/batch-delete`.

    Soft delete by default; pass `?hard=true` query param for hard delete.
    Transaction is all-or-nothing — any missing id rolls back the batch.
    """

    ids: list[str] = Field(
        ..., min_length=1, max_length=MAX_BATCH_SIZE,
        description="UUIDs to delete. All must exist or the batch is rolled back.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {"ids": ["uuid-1", "uuid-2", "uuid-3"]}
        }
    }
