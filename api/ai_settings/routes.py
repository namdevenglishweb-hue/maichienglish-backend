"""Admin API for runtime-editable AI generation settings (migration 0022).

GET/PUT the singleton config so admins can change provider/model/max_tokens/
self-review-rounds without an env edit + redeploy. Resolution at generation
time stays: per-request override > this config > env default.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user, require_admin
from services.ai.generator import KNOWN_PROVIDERS
from services.ai_settings_service import ai_settings_service
from services.user_service import user_service

from .schemas import (
    AISettingsResponse,
    AISettingsStored,
    AISettingsUpdate,
    AISettingsView,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/ai-settings",
    tags=["Admin · AI Settings"],
    dependencies=[Depends(require_admin)],
)

# API camelCase field → DB snake_case column
_FIELD_MAP = {
    "provider": "provider",
    "model": "model",
    "maxTokens": "max_tokens",
    "selfReviewRounds": "self_review_rounds",
}


def _to_view(eff: dict) -> AISettingsView:
    return AISettingsView(
        provider=eff["provider"], model=eff["model"],
        maxTokens=eff["max_tokens"], selfReviewRounds=eff["self_review_rounds"],
    )


def _to_stored(row: dict | None) -> AISettingsStored:
    row = row or {}
    return AISettingsStored(
        provider=row.get("provider"), model=row.get("model"),
        maxTokens=row.get("max_tokens"), selfReviewRounds=row.get("self_review_rounds"),
    )


@admin_router.get("", response_model=AISettingsResponse)
async def get_ai_settings():
    eff = await ai_settings_service.get_effective()
    stored = await ai_settings_service.get_stored()
    return AISettingsResponse(effective=_to_view(eff), stored=_to_stored(stored))


@admin_router.put("", response_model=AISettingsResponse)
async def update_ai_settings(
    request: AISettingsUpdate, current_user: dict = Depends(get_current_user)
):
    if request.provider is not None and request.provider not in KNOWN_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider {request.provider!r}; allowed: {', '.join(KNOWN_PROVIDERS)}",
        )
    # Only the fields actually sent are changed (null clears → env default).
    sent = request.model_dump(exclude_unset=True)
    fields = {_FIELD_MAP[k]: v for k, v in sent.items()}

    profile = await user_service.get_by_email(current_user["sub"])
    eff = await ai_settings_service.update(
        fields, updated_by=profile["id"] if profile else None
    )
    stored = await ai_settings_service.get_stored()
    return AISettingsResponse(effective=_to_view(eff), stored=_to_stored(stored))
