from typing import Literal, Optional

from pydantic import BaseModel, Field

LevelLiteral = Literal["primary", "secondary", "KET", "PET", "IELTS"]
SkillLiteral = Literal["listening", "reading"]


class ExamCreate(BaseModel):
    """Body for POST /api/exams (admin only)."""

    title: str = Field(..., min_length=1, description="Exam title shown in UI")
    level: LevelLiteral = Field(..., description="primary / secondary / KET / PET / IELTS")
    skill: SkillLiteral = Field(..., description="listening or reading")
    duration_minutes: int = Field(default=45, gt=0, description="Time limit in minutes")
    description: Optional[str] = None
    audio_url: Optional[str] = Field(
        default=None,
        description="Shared listening audio (listening exams only)",
    )
    passage: Optional[str] = Field(
        default=None, description="Reading passage text (reading exams only)"
    )
    max_audio_plays: int = Field(
        default=3, ge=0, description="Cap on student replays for listening audio"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "KET Listening Practice 01",
                "level": "KET",
                "skill": "listening",
                "duration_minutes": 30,
                "description": "Two-part listening with 10 multiple-choice questions.",
                "audio_url": "https://[project].supabase.co/storage/v1/object/sign/audio/ket01.mp3",
                "passage": None,
                "max_audio_plays": 3,
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
    audio_url: Optional[str] = None
    passage: Optional[str] = None
    max_audio_plays: Optional[int] = Field(default=None, ge=0)


class ExamView(BaseModel):
    """Exam payload returned to clients."""

    id: str
    title: str
    level: str
    skill: str
    durationMinutes: int
    description: Optional[str] = None
    audioUrl: Optional[str] = None
    passage: Optional[str] = None
    maxAudioPlays: int
    isPublished: bool
    createdBy: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    deletedAt: Optional[str] = None


class ExamResponseData(BaseModel):
    exam: ExamView


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
