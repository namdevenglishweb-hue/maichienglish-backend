from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SectionTypeLiteral = Literal["multiple_choice", "fill_blank", "matching"]


class SectionMaterial(BaseModel):
    """A passage entry inside `sections.materials`.

    Only the `text` variant is supported today. Gap markers in `content` use
    the convention `{{gap:N}}` where N is the `position` of a question within
    the same section. The frontend parses these markers and renders an input
    bound to that question.
    """

    type: Literal["text"] = "text"
    label: Optional[str] = Field(
        default=None,
        description="Optional heading shown above the passage (e.g. 'From: Bea').",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Passage body. May embed {{gap:N}} markers.",
    )


class SectionCreate(BaseModel):
    """Body for POST /api/exams/{exam_id}/sections (admin only)."""

    partLabel: Optional[str] = Field(
        default=None, description="Display label, e.g. 'Part 1'"
    )
    type: Optional[SectionTypeLiteral] = Field(
        default=None,
        description=(
            "FE rendering hint. 'matching' → shared-options table; "
            "'multiple_choice'/'fill_blank' → vertical list; null → mixed."
        ),
    )
    instructions: Optional[str] = Field(
        default=None, description="Rubric shown to the student"
    )
    materials: list[SectionMaterial] = Field(
        default_factory=list,
        description="Passage entries. Empty list for sections without passages.",
    )
    audioUrl: Optional[str] = Field(
        default=None,
        description="Listening audio URL (listening sections only).",
    )
    maxAudioPlays: Optional[int] = Field(
        default=None,
        ge=0,
        description="Per-section cap on student replays (listening only).",
    )
    position: Optional[int] = Field(
        default=None,
        ge=1,
        description="Order within the exam. If omitted, server appends to the end.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "partLabel": "Part 5",
                "type": "fill_blank",
                "instructions": "For each question, write the correct answer. Write ONE word for each gap.",
                "materials": [
                    {
                        "type": "text",
                        "label": "From: Bea  |  To: Tania",
                        "content": "How are things? Are you busy {{gap:1}} the moment?",
                    }
                ],
                "audioUrl": None,
                "maxAudioPlays": None,
            }
        }
    }


class SectionUpdate(BaseModel):
    """Body for PUT /api/sections/{section_id}. Omit a field to leave it unchanged."""

    partLabel: Optional[str] = None
    type: Optional[SectionTypeLiteral] = None
    instructions: Optional[str] = None
    materials: Optional[list[SectionMaterial]] = None
    audioUrl: Optional[str] = None
    maxAudioPlays: Optional[int] = Field(default=None, ge=0)
    position: Optional[int] = Field(default=None, ge=1)


class SectionQuestionPreview(BaseModel):
    """Question shape returned when `?include=questions` is requested on a section.

    `questionData` may have correct-answer fields stripped for non-privileged
    callers — kept as `dict[str, Any]` so both shapes round-trip cleanly.
    """

    id: str
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int


class SectionView(BaseModel):
    """Section payload returned to clients.

    `questions` is populated only when the endpoint was called with
    `?include=questions`. It's `None` otherwise so the OpenAPI schema makes
    the optional nesting explicit.
    """

    id: str
    examId: str
    position: int
    partLabel: Optional[str] = None
    type: Optional[SectionTypeLiteral] = Field(
        default=None,
        description="FE rendering hint; 'matching' signals shared-options table layout.",
    )
    instructions: Optional[str] = None
    materials: list[dict[str, Any]] = Field(default_factory=list)
    audioUrl: Optional[str] = None
    maxAudioPlays: Optional[int] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    deletedAt: Optional[str] = None
    questions: Optional[list[SectionQuestionPreview]] = Field(
        default=None,
        description="Populated only when ?include=questions was requested.",
    )


class SectionResponseData(BaseModel):
    section: SectionView


class SectionResponse(BaseModel):
    """Wrapped response for single-section endpoints."""

    status: int = 200
    data: SectionResponseData


class SectionListResponseData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[SectionView]


class SectionListResponse(BaseModel):
    """Wrapped response for GET /api/exams/{exam_id}/sections."""

    status: int = 200
    data: SectionListResponseData
