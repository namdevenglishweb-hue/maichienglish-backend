"""Request/response schemas for AI exam generation (admin, §14)."""

from typing import Any, Optional

from pydantic import BaseModel, Field

from api.exams.schemas import ExamView


class GenerateExamRequest(BaseModel):
    """Mode 1 — whole exam, auto-save (POST /api/admin/exam-generations)."""

    sourceExamId: str
    k: int = Field(..., ge=1, le=5, description="Variation level 1..5 (§3).")
    title: Optional[str] = Field(
        default=None, description="New exam title. Default '{source} (AI K{k})'."
    )
    sectionPrompts: Optional[dict[str, str]] = Field(
        default=None,
        description="Per-section ad-hoc prompt (B, §10.2), keyed by source sectionId.",
    )


class GenerateSectionRequest(BaseModel):
    """Mode 2 single part (POST .../section)."""

    sourceSectionId: str
    k: int = Field(..., ge=1, le=5)
    sectionPrompt: Optional[str] = None


class PreviewRequest(BaseModel):
    """Mode 2 all-parts preview, no save (POST .../preview)."""

    sourceExamId: str
    k: int = Field(..., ge=1, le=5)
    sectionPrompts: Optional[dict[str, str]] = None


class AssembleRequest(BaseModel):
    """Mode 2 Save (POST .../assembled-exam). `sections` are generated section
    payloads held client-side; re-validated server-side by create_exam_nested."""

    sourceExamId: str
    title: Optional[str] = None
    k: Optional[int] = Field(default=None, ge=1, le=5)
    sections: list[dict[str, Any]] = Field(..., min_length=1)
    sectionPrompts: Optional[dict[str, str]] = None


class JobAcceptedResponse(BaseModel):
    jobId: str
    status: str = "pending"


class JobView(BaseModel):
    """Polled job state (§14.2). `report` shape varies by scope."""

    jobId: str
    scope: str
    status: str
    sourceExamId: str
    targetSectionId: Optional[str] = None
    k: int
    title: Optional[str] = None
    sectionsTotal: Optional[int] = None
    sectionsDone: int = 0
    currentSection: Optional[int] = None
    resultExamId: Optional[str] = None
    report: Optional[dict[str, Any]] = None
    abortedReason: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    finishedAt: Optional[str] = None


class JobListResponse(BaseModel):
    status: int = 200
    data: dict[str, list[JobView]]


class AssembledExamData(BaseModel):
    exam: ExamView  # camelCase, consistent with the rest of the exam API
    warning: Optional[str] = None


class AssembledExamResponse(BaseModel):
    status: int = 201
    data: AssembledExamData
