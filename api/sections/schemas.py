from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from api.common import MAX_BATCH_SIZE
from api.questions.schemas import QuestionCreate

SectionTypeLiteral = Literal[
    "multiple_choice", "fill_blank", "matching", "multiple_choice_shared",
    "writing", "speaking",   # opened in migration 0011; rendering-hint values
                             # only — manual-grading flow ships later
                             # (see WRITING_SPEAKING.md)
]


class TextMaterial(BaseModel):
    """A passage block inside `sections.materials`.

    Gap markers in `content` use the convention `{{gap:N}}` where N is the
    `position` of a question within the same section. The frontend parses
    these markers and replaces each with an input bound to that question.
    """

    type: Literal["text"]
    label: Optional[str] = Field(
        default=None,
        description="Optional heading shown above the passage (e.g. 'From: Bea').",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Passage body. May embed {{gap:N}} markers.",
    )


class ImageMaterial(BaseModel):
    """An image block inside `sections.materials` (diagram, form, illustration)."""

    type: Literal["image"]
    label: Optional[str] = Field(
        default=None,
        description="Optional caption shown above the image.",
    )
    url: str = Field(..., min_length=1, description="Image asset URL.")
    alt: Optional[str] = Field(
        default=None,
        description="Accessibility description. FE should warn (but not block) when missing.",
    )


class AudioMaterial(BaseModel):
    """An audio block inside `sections.materials`.

    Per-audio replay tracking lives in `attempt_section_state.audio_play_counts`
    keyed by the material's index within this section. The cap value is
    `sections.max_audio_plays` — shared across all audio materials in this
    section but counted independently per audio.
    """

    type: Literal["audio"]
    label: Optional[str] = Field(
        default=None,
        description="Optional label shown next to the play control.",
    )
    url: str = Field(..., min_length=1, description="Audio asset URL.")


# Discriminated union — Pydantic routes each dict to the right class based
# on its `type` field. FE/admin send and receive items of this shape inside
# `materials`.
SectionMaterial = Annotated[
    Union[TextMaterial, ImageMaterial, AudioMaterial],
    Field(discriminator="type"),
]


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
        description=(
            "Typed content blocks shown above the questions. Each entry is "
            "one of: TextMaterial / ImageMaterial / AudioMaterial. Order is "
            "significant — audio counters are addressed by index."
        ),
    )
    maxAudioPlays: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Section-wide cap value applied INDEPENDENTLY to each audio "
            "material in this section (per-audio counter, shared cap value). "
            "Null = unlimited."
        ),
    )
    position: Optional[int] = Field(
        default=None,
        ge=1,
        description="Order within the exam. If omitted, server appends to the end.",
    )
    questions: Optional[list[QuestionCreate]] = Field(
        default=None,
        max_length=MAX_BATCH_SIZE,
        description=(
            "Optional inline questions. When provided, the section and all "
            "child questions are created in one transaction. Question "
            "positions are server-assigned 1..N in array order (the `position` "
            "field on each item is ignored in this nested context). Gap "
            "markers in `materials` are validated against the resulting "
            "question positions — broken markers reject the whole batch."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "partLabel": "Part 2",
                "type": "fill_blank",
                "instructions": "You will hear some information about city bus tours. Listen and complete the form.",
                "materials": [
                    {
                        "type": "audio",
                        "label": "Track 1",
                        "url": "https://[project].supabase.co/.../ket-l-p2.mp3"
                    },
                    {
                        "type": "image",
                        "label": "City Bus Tours form",
                        "url": "https://[project].supabase.co/.../ket-l-p2-form.png",
                        "alt": "Form with 5 blank fields to fill in",
                    },
                    {
                        "type": "text",
                        "content": "Name of guide: {{gap:1}}",
                    }
                ],
                "maxAudioPlays": 3,
            }
        }
    }


class SectionUpdate(BaseModel):
    """Body for PUT /api/sections/{section_id}. Omit a field to leave it unchanged."""

    partLabel: Optional[str] = None
    type: Optional[SectionTypeLiteral] = None
    instructions: Optional[str] = None
    materials: Optional[list[SectionMaterial]] = None
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
    createdCounts: Optional[dict[str, int]] = Field(
        default=None,
        description=(
            "Populated only by nested POST that creates child questions inline."
        ),
    )


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


# ---------------------------------------------------------------------------
# Batch operations (capped at MAX_BATCH_SIZE items per request)
# ---------------------------------------------------------------------------


class SectionBatchUpdateItem(SectionUpdate):
    """One section patch in a batch update. `id` identifies the target row;
    all other fields follow `SectionUpdate` semantics."""

    id: str = Field(..., description="UUID of the section to update")


class SectionBatchUpdateRequest(BaseModel):
    """Body for PUT /api/sections/batch."""

    updates: list[SectionBatchUpdateItem] = Field(
        ..., min_length=1, max_length=MAX_BATCH_SIZE,
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "updates": [
                    {"id": "uuid-1", "instructions": "Updated rubric"},
                    {"id": "uuid-2", "partLabel": "Part 2", "position": 2},
                ]
            }
        }
    }


class SectionBatchUpdateResponse(BaseModel):
    status: int = 200
    data: SectionListResponseData
