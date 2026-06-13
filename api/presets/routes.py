"""Part presets (read-only) — FE reads this to build the Part dropdown and to
know each Part's canonical structure. Presets are code constants
(services/presets.py); this endpoint never writes. See docs/exam-part-presets/.
"""

from fastapi import APIRouter, Depends

from dependencies import get_current_user
from services.presets import list_presets

from .schemas import PresetListResponse

router = APIRouter(prefix="/api/presets", tags=["Presets"])


# Auth: any authenticated user (admin + teacher per amendment §2.1 "public cho
# admin/teacher role"; data is non-sensitive Cambridge format constants).
@router.get("", response_model=PresetListResponse)
async def get_presets(current_user: dict = Depends(get_current_user)):
    """All Cambridge Part presets (MC-only this round: PET_R_P3, KET_R_P3)."""
    return {"presets": list_presets()}
