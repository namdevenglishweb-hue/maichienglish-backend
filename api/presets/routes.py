"""Part presets (read-only) — FE reads this to build the Part dropdown and to
know each Part's canonical structure. Presets are code constants
(services/presets.py); this endpoint never writes. See docs/exam-part-presets/.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user, require_teacher_or_admin
from services.exceptions import ValidationError
from services.presets import list_presets, resolve_preset, scaffold_section_from_preset

from .schemas import (
    PresetListResponse,
    ScaffoldSectionRequest,
    ScaffoldSectionResponse,
)

router = APIRouter(prefix="/api/presets", tags=["Presets"])


# Auth: any authenticated user (admin + teacher per amendment §2.1 "public cho
# admin/teacher role"; data is non-sensitive Cambridge format constants).
@router.get("", response_model=PresetListResponse)
async def get_presets(current_user: dict = Depends(get_current_user)):
    """All Cambridge Part presets (full KET/PET catalogue; aiGenSupported flags
    which can be AI-generated this round)."""
    return {"presets": list_presets()}


@router.post(
    "/scaffold-section",
    response_model=ScaffoldSectionResponse,
    status_code=status.HTTP_200_OK,
)
async def scaffold_section(
    request: ScaffoldSectionRequest,
    current_user: dict = Depends(require_teacher_or_admin),
):
    """B3 — build an empty-but-valid section TEMPLATE from a preset (NOT
    persisted; the FE drops it into an exam and saves). Unknown part_code → 400.
    """
    try:
        preset = resolve_preset(request.partCode)
    except ValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    if preset is None:                       # empty/absent part_code
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="partCode is required")
    return ScaffoldSectionResponse(section=scaffold_section_from_preset(preset))
