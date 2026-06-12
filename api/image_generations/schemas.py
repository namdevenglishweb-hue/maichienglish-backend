"""Request/response schemas for AI image generation (admin, §7)."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class ImageExamContext(BaseModel):
    level: Optional[str] = None
    skill: Optional[str] = None


class GenerateImageRequest(BaseModel):
    description: str = Field(..., min_length=1, description="What the image must depict.")
    sourceImageUrl: Optional[str] = Field(
        default=None, description="Old image URL → edit mode (keeps layout/text)."
    )
    examContext: Optional[ImageExamContext] = Field(
        default=None, description="Optional style hint {level, skill}."
    )


class ImageJobAcceptedResponse(BaseModel):
    """202 body for image generation — poll GET /{jobId} for the result."""

    jobId: str = Field(..., description="Id of the created background image job.")
    status: str = Field(default="pending", description="Initial job status (always 'pending').")


class ImageJobView(BaseModel):
    """Polled image-generation job state."""

    jobId: str = Field(..., description="Job id.")
    status: str = Field(..., description="pending | running | done | failed.")
    mode: str = Field(..., description="'create' or 'edit' (edit when sourceImageUrl was given).")
    description: str = Field(..., description="What the image was asked to depict.")
    sourceImageUrl: Optional[str] = Field(
        default=None, description="Original image URL for edit mode, if any."
    )
    resultUrl: Optional[str] = Field(
        default=None, description="Uploaded result image URL once the job finishes."
    )
    report: Optional[dict[str, Any]] = Field(
        default=None, description="Diagnostics/result payload from the generation run."
    )
    createdAt: Optional[str] = Field(default=None, description="ISO-8601 creation time.")
    updatedAt: Optional[str] = Field(default=None, description="ISO-8601 last-update time.")
    finishedAt: Optional[str] = Field(default=None, description="ISO-8601 completion time.")


class ImageJobListResponseData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[ImageJobView]


class ImageJobListResponse(BaseModel):
    """Wrapped response for GET /api/admin/image-generations."""

    status: int = 200
    data: ImageJobListResponseData
