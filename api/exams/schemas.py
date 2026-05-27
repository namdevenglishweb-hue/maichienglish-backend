from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from api.common import MAX_BATCH_SIZE
from api.sections.schemas import SectionCreate

LevelLiteral = Literal["primary", "secondary", "KET", "PET", "IELTS"]
SkillLiteral = Literal["listening", "reading"]


class ExamCreate(BaseModel):
    """Body for POST /api/exams (admin only).

    Optionally accepts a `sections` array; each section may itself nest
    `questions`. When `sections` is provided, the whole tree is created in
    one transaction with server-assigned positions (1..N in array order).
    """

    title: str = Field(..., min_length=1, description="Exam title shown in UI")
    level: LevelLiteral = Field(..., description="primary / secondary / KET / PET / IELTS")
    skill: SkillLiteral = Field(..., description="listening or reading")
    duration_minutes: int = Field(default=45, gt=0, description="Time limit in minutes")
    description: Optional[str] = None
    sections: Optional[list[SectionCreate]] = Field(
        default=None,
        max_length=MAX_BATCH_SIZE,
        description=(
            "Optional inline sections. When provided, the exam, every "
            "section, and every question (nested under each section) are "
            "created in one transaction. Section positions are assigned "
            "1..N in array order; same rule for questions within each "
            "section. Gap markers in each section's materials are validated "
            "against that section's question positions."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "KET Reading Practice 01",
                "level": "KET",
                "skill": "reading",
                "duration_minutes": 60,
                "description": "Reading + writing paper, 5 parts.",
            }
        }
    }


class ExamUpdate(BaseModel):
    """Body for PUT /api/exams/{exam_id}. Omit a field to leave it unchanged."""

    title: Optional[str] = Field(default=None, min_length=1)
    level: Optional[LevelLiteral] = None
    skill: Optional[SkillLiteral] = None
    duration_minutes: Optional[int] = Field(default=None, gt=0)
    description: Optional[str] = None


class ExamQuestionPreview(BaseModel):
    """Question shape nested under a section when `?include=sections` is requested.

    `questionData` is `dict[str, Any]` because correct-answer fields are
    stripped for non-privileged callers — both shapes round-trip cleanly.
    """

    id: str
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int


class ExamSectionPreview(BaseModel):
    """Section shape nested under an exam when `?include=sections` is requested."""

    id: str
    position: int
    partLabel: Optional[str] = None
    type: Optional[Literal["multiple_choice", "fill_blank", "matching", "multiple_choice_shared"]] = Field(
        default=None,
        description="FE rendering hint; 'matching' signals shared-options table layout.",
    )
    instructions: Optional[str] = None
    materials: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Typed blocks (text/image/audio). Audio entries carry `url`.",
    )
    maxAudioPlays: Optional[int] = None
    questions: list[ExamQuestionPreview] = Field(default_factory=list)


class ExamView(BaseModel):
    """Top-level exam metadata returned to clients.

    `sections` is populated only when the endpoint was called with
    `?include=sections`. `None` otherwise so the OpenAPI schema makes the
    optional nesting explicit.
    """

    id: str
    title: str
    level: str
    skill: str
    durationMinutes: int
    description: Optional[str] = None
    isPublished: bool
    createdBy: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    deletedAt: Optional[str] = None
    sections: Optional[list[ExamSectionPreview]] = Field(
        default=None,
        description="Populated only when ?include=sections was requested.",
    )


class ExamResponseData(BaseModel):
    exam: ExamView
    createdCounts: Optional[dict[str, int]] = Field(
        default=None,
        description=(
            "Populated only when the exam was created via nested POST that "
            "also inserted sections/questions. Shape: "
            "{'sections': int, 'questions': int}."
        ),
    )


class ExamResponse(BaseModel):
    """Wrapped response for single-exam endpoints."""

    status: int = 200
    data: ExamResponseData


class ExamListResponseData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[ExamView]


class ExamListResponse(BaseModel):
    """Wrapped response for GET /api/exams."""

    status: int = 200
    data: ExamListResponseData
