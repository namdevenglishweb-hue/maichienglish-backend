"""Schemas for admin per-type prompt config (§10, §14.6)."""

from typing import Optional

from pydantic import BaseModel, Field


class PromptUpsertRequest(BaseModel):
    additionalPrompt: str = Field(..., min_length=1)


class PromptView(BaseModel):
    type: str
    additionalPrompt: str
    updatedAt: Optional[str] = None
    updatedBy: Optional[str] = None


class PromptListResponse(BaseModel):
    status: int = 200
    data: dict[str, list[PromptView]]


class PromptResponse(BaseModel):
    status: int = 200
    data: PromptView
