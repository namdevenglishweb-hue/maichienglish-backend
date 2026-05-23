from typing import Any, Optional

from pydantic import BaseModel, Field


class AttemptStartRequest(BaseModel):
    """Body for POST /api/attempts."""

    examId: str = Field(..., description="UUID of a published exam")

    model_config = {
        "json_schema_extra": {"example": {"examId": "uuid-of-exam"}}
    }


class AttemptExamView(BaseModel):
    """Exam metadata returned at attempt start."""

    id: str
    title: str
    level: str
    skill: str
    durationMinutes: int
    description: Optional[str] = None
    audioUrl: Optional[str] = None
    passage: Optional[str] = None
    maxAudioPlays: int


class AttemptQuestionView(BaseModel):
    """A question as shown to a student during an attempt (correct answers stripped)."""

    id: str
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int


class AttemptView(BaseModel):
    """Attempt row payload."""

    id: str
    userId: str
    examId: str
    score: Optional[float] = None
    totalPoints: Optional[float] = None
    percentage: Optional[float] = None
    timeSpentSeconds: Optional[int] = None
    audioPlayCount: int
    startedAt: Optional[str] = None
    submittedAt: Optional[str] = None


class AttemptStartResponseData(BaseModel):
    attemptId: str
    exam: AttemptExamView
    questions: list[AttemptQuestionView]
    startedAt: Optional[str] = None


class AttemptStartResponse(BaseModel):
    """Wrapped POST /api/attempts response."""

    status: int = 200
    data: AttemptStartResponseData


class AnswerInput(BaseModel):
    questionId: str
    studentAnswer: Any = None  # int / str / list — depends on question type


class AttemptSubmitRequest(BaseModel):
    """Body for POST /api/attempts/{attempt_id}/submit."""

    answers: list[AnswerInput] = Field(default_factory=list)
    timeSpentSeconds: Optional[int] = Field(default=None, ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "answers": [
                    {"questionId": "uuid-q1", "studentAnswer": 1},
                    {"questionId": "uuid-q2", "studentAnswer": "nine"},
                    {"questionId": "uuid-q3", "studentAnswer": [[0, 0], [1, 1]]},
                ],
                "timeSpentSeconds": 1200,
            }
        }
    }


class AttemptSubmitResponseData(BaseModel):
    score: Optional[float] = None
    totalPoints: Optional[float] = None
    percentage: Optional[float] = None
    submittedAt: Optional[str] = None


class AttemptSubmitResponse(BaseModel):
    """Wrapped POST /api/attempts/{attempt_id}/submit response."""

    status: int = 200
    data: AttemptSubmitResponseData


class AnswerView(BaseModel):
    """Per-question breakdown returned by GET /api/attempts/{id}."""

    answerId: str
    questionId: str
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int
    studentAnswer: Any = None
    isCorrect: Optional[bool] = None
    pointsEarned: int = 0


class AttemptDetailExam(BaseModel):
    id: str
    title: str
    level: str
    skill: str


class AttemptDetailData(BaseModel):
    attempt: AttemptView
    exam: AttemptDetailExam
    answers: list[AnswerView]


class AttemptDetailResponse(BaseModel):
    """Wrapped GET /api/attempts/{id} response."""

    status: int = 200
    data: AttemptDetailData


class AttemptHistoryItem(BaseModel):
    id: str
    examId: str
    examTitle: str
    examLevel: str
    examSkill: str
    score: Optional[float] = None
    totalPoints: Optional[float] = None
    percentage: Optional[float] = None
    timeSpentSeconds: Optional[int] = None
    startedAt: Optional[str] = None
    submittedAt: Optional[str] = None


class AttemptHistoryData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[AttemptHistoryItem]


class AttemptHistoryResponse(BaseModel):
    """Wrapped GET /api/attempts/history response."""

    status: int = 200
    data: AttemptHistoryData


class AudioPlayResponseData(BaseModel):
    audioPlayCount: int
    maxAudioPlays: int
    remainingPlays: int


class AudioPlayResponse(BaseModel):
    """Wrapped POST /api/attempts/{id}/audio-play response."""

    status: int = 200
    data: AudioPlayResponseData
