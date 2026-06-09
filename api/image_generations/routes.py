"""AI image generation HTTP routes (admin-only, §7).

POST creates a job + runs it in the background (FastAPI BackgroundTasks); FE
polls. Stateless: returns the uploaded image URL in the job result — FE applies
it to the material. Gated by IMAGE_GENERATION_ENABLED (POST → 409 when off).
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from config.settings import get_settings
from dependencies import get_current_user, require_admin
from services.exceptions import NotFoundError
from services.image_job_service import image_job_service, run_image_job
from services.user_service import user_service

from .schemas import (
    GenerateImageRequest,
    ImageJobAcceptedResponse,
    ImageJobListResponse,
    ImageJobView,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/image-generations",
    tags=["Admin · AI Image Generation"],
    dependencies=[Depends(require_admin)],
)


async def _admin_id(current_user: dict) -> str | None:
    profile = await user_service.get_by_email(current_user["sub"])
    return profile["id"] if profile else None


@admin_router.post(
    "", response_model=ImageJobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def generate_image(
    request: GenerateImageRequest,
    background: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Generate one image from a description (edit if sourceImageUrl given)."""
    if not get_settings().image_generation_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="image generation is disabled")

    job = await image_job_service.create_job(
        description=request.description, source_image_url=request.sourceImageUrl,
        created_by=await _admin_id(current_user),
    )
    background.add_task(
        run_image_job, job_id=job["jobId"], description=request.description,
        source_image_url=request.sourceImageUrl,
        exam_context=request.examContext.model_dump() if request.examContext else None,
    )
    return ImageJobAcceptedResponse(jobId=job["jobId"], status=job["status"])


@admin_router.get("/{job_id}", response_model=ImageJobView)
async def get_job(job_id: str):
    try:
        return ImageJobView(**await image_job_service.get_job(job_id))
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@admin_router.get("", response_model=ImageJobListResponse)
async def list_jobs(
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    jobs = await image_job_service.list_jobs(status=status_, limit=limit, offset=offset)
    return ImageJobListResponse(data={"items": [ImageJobView(**j) for j in jobs]})
