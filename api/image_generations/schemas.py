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
    jobId: str
    status: str = "pending"


class ImageJobView(BaseModel):
    jobId: str
    status: str
    mode: str
    description: str
    sourceImageUrl: Optional[str] = None
    resultUrl: Optional[str] = None
    report: Optional[dict[str, Any]] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    finishedAt: Optional[str] = None


class ImageJobListResponse(BaseModel):
    status: int = 200
    data: dict[str, list[ImageJobView]]
