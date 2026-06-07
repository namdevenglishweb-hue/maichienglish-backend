"""Admin CRUD for per-type generation prompts (source A, §10 / §14.6)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user, require_admin
from services.exceptions import NotFoundError, ValidationError
from services.section_type_prompt_service import section_type_prompt_service
from services.user_service import user_service

from .schemas import (
    PromptListResponse,
    PromptResponse,
    PromptUpsertRequest,
    PromptView,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/section-type-prompts",
    tags=["Admin · AI Exam Generation"],
    dependencies=[Depends(require_admin)],
)


def _view(p: dict) -> PromptView:
    return PromptView(
        type=p["type"], additionalPrompt=p["additional_prompt"],
        updatedAt=p["updated_at"], updatedBy=p["updated_by"],
    )


@admin_router.get("", response_model=PromptListResponse)
async def list_prompts():
    rows = await section_type_prompt_service.list_prompts()
    return PromptListResponse(data={"items": [_view(p) for p in rows]})


@admin_router.put("/{type}", response_model=PromptResponse)
async def upsert_prompt(
    type: str,
    request: PromptUpsertRequest,
    current_user: dict = Depends(get_current_user),
):
    profile = await user_service.get_by_email(current_user["sub"])
    try:
        row = await section_type_prompt_service.upsert(
            type, request.additionalPrompt,
            updated_by=profile["id"] if profile else None,
        )
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return PromptResponse(data=_view(row))


@admin_router.delete("/{type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(type: str):
    try:
        await section_type_prompt_service.delete(type)
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
