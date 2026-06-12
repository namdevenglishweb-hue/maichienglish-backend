"""Schemas for runtime-editable AI generation settings (admin)."""

from typing import Optional

from pydantic import BaseModel, Field


class AISettingsView(BaseModel):
    """Effective config — what generation will actually use."""

    provider: str = Field(..., description="Effective AI provider id.")
    model: str = Field(..., description="Effective model id for that provider.")
    maxTokens: int = Field(..., description="Effective max output tokens per generation call.")
    selfReviewRounds: int = Field(..., description="Effective number of self-review passes.")


class AISettingsStored(BaseModel):
    """Raw stored overrides — null means 'use the env default'."""

    provider: Optional[str] = Field(default=None, description="Stored provider override, or null.")
    model: Optional[str] = Field(default=None, description="Stored model override, or null.")
    maxTokens: Optional[int] = Field(
        default=None, description="Stored max-tokens override, or null."
    )
    selfReviewRounds: Optional[int] = Field(
        default=None, description="Stored self-review-rounds override, or null."
    )


class AISettingsResponse(BaseModel):
    """GET/PUT result — the effective config plus the raw stored overrides."""

    effective: AISettingsView = Field(..., description="Config generation will actually use.")
    stored: AISettingsStored = Field(..., description="Raw DB overrides (nulls fall back to env).")


class AISettingsUpdate(BaseModel):
    """PUT body — only the fields you send are changed. Send null to clear a
    field (it then falls back to the env default)."""

    provider: Optional[str] = Field(default=None, description="New provider, or null to clear.")
    model: Optional[str] = Field(default=None, description="New model id, or null to clear.")
    maxTokens: Optional[int] = Field(
        default=None, gt=0, description="New max output tokens (>0), or null to clear."
    )
    selfReviewRounds: Optional[int] = Field(
        default=None, ge=0, description="New self-review rounds (>=0), or null to clear."
    )
