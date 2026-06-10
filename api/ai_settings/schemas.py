"""Schemas for runtime-editable AI generation settings (admin)."""

from typing import Optional

from pydantic import BaseModel, Field


class AISettingsView(BaseModel):
    """Effective config — what generation will actually use."""

    provider: str
    model: str
    maxTokens: int
    selfReviewRounds: int


class AISettingsStored(BaseModel):
    """Raw stored overrides — null means 'use the env default'."""

    provider: Optional[str] = None
    model: Optional[str] = None
    maxTokens: Optional[int] = None
    selfReviewRounds: Optional[int] = None


class AISettingsResponse(BaseModel):
    effective: AISettingsView
    stored: AISettingsStored


class AISettingsUpdate(BaseModel):
    """PUT body — only the fields you send are changed. Send null to clear a
    field (it then falls back to the env default)."""

    provider: Optional[str] = None
    model: Optional[str] = None
    maxTokens: Optional[int] = Field(default=None, gt=0)
    selfReviewRounds: Optional[int] = Field(default=None, ge=0)
