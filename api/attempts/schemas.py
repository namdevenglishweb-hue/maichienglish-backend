from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

AttemptMode = Literal["practice", "real"]


class AttemptStartRequest(BaseModel):
    """Body for POST /api/attempts."""

    examId: str = Field(..., description="UUID of a published exam")
    mode: AttemptMode = Field(
        default="practice",
        description="'practice' (thi thử, mặc định) hoặc 'real' (thi thật: "
        "audio nghe 1 lần + không resume).",
    )

    model_config = {
        "json_schema_extra": {
            "example": {"examId": "uuid-of-exam", "mode": "practice"}
        }
    }


class AttemptQuestionView(BaseModel):
    """A question as shown to a student during an attempt (correct answers stripped)."""

    id: str
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int


class AttemptSectionView(BaseModel):
    """A section as shown to a student during an attempt."""

    id: str
    position: int
    partLabel: Optional[str] = None
    type: Optional[str] = Field(
        default=None,
        description="FE rendering hint mirrored from section.type.",
    )
    instructions: Optional[str] = None
    materials: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Typed content blocks (text/image/audio). Audio entries carry "
            "`url`; FE calls POST /attempts/{aid}/sections/{sid}/audio-play"
            "?materialIndex=<index> before each play, gated by maxAudioPlays."
        ),
    )
    maxAudioPlays: Optional[int] = Field(
        default=None,
        description="Section-wide cap value applied INDEPENDENTLY per audio material.",
    )
    audioPlayCounts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Per-material play count, keyed by stringified material_index "
            "('0', '1', ...). Material indices absent from the map mean "
            "count=0 (audio never played in this attempt yet). "
            "FE compute remaining = maxAudioPlays - (audioPlayCounts[idx] or 0). "
            "See ATTEMPT_LIFECYCLE.md §4.6.1."
        ),
    )
    questions: list[AttemptQuestionView] = Field(default_factory=list)


class AttemptExamView(BaseModel):
    """Exam metadata + nested sections returned at attempt start."""

    id: str
    title: str
    level: str
    skill: str
    durationMinutes: int
    description: Optional[str] = None
    sections: list[AttemptSectionView] = Field(default_factory=list)


class AttemptView(BaseModel):
    """Attempt row payload."""

    id: str
    userId: str
    examId: str
    score: Optional[float] = None
    totalPoints: Optional[float] = None
    percentage: Optional[float] = None
    timeSpentSeconds: Optional[int] = None
    isAbandoned: bool = False
    isFullyGraded: bool = True
    mode: AttemptMode = "practice"
    startedAt: Optional[str] = None
    submittedAt: Optional[str] = None


class SavedAnswerView(BaseModel):
    """A previously-saved (ungraded) answer returned at resume."""

    questionId: str
    studentAnswer: Any = None


class AttemptStartResponseData(BaseModel):
    """Payload returned by POST /api/attempts (Case A new, Case B resume).

    `isResume` lets the FE distinguish: false → fresh start (201 wire
    status), true → resume of an existing in-progress attempt (200 wire
    status). `savedAnswers` is empty in the new case and populated with
    previously-saved answers in the resume case.
    """

    attemptId: str
    isResume: bool = False
    exam: AttemptExamView
    savedAnswers: list[SavedAnswerView] = Field(default_factory=list)
    startedAt: Optional[str] = None


class AttemptStartResponse(BaseModel):
    """Wrapped POST /api/attempts response.

    Wire `status` carries 201 (new) or 200 (resume) and matches the HTTP
    status code FastAPI returns. See ATTEMPT_LIFECYCLE.md §4.1."""

    status: int = 201
    data: AttemptStartResponseData


class ActiveAttemptData(BaseModel):
    """Summary of the user's current in-progress attempt."""

    attemptId: str
    examId: str
    examTitle: str
    examLevel: str
    examSkill: str
    mode: AttemptMode = "practice"
    startedAt: Optional[str] = None
    savedAnswerCount: int = 0


class ActiveAttemptResponse(BaseModel):
    """Wrapped GET /api/attempts/active response."""

    status: int = 200
    data: ActiveAttemptData


class AnswerInput(BaseModel):
    questionId: str
    studentAnswer: Any = None  # int / str / list — depends on question type


class AttemptSaveRequest(BaseModel):
    """Body for PATCH /api/attempts/{attempt_id}/answers (manual save)."""

    answers: list[AnswerInput] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "answers": [
                    {"questionId": "uuid-q1", "studentAnswer": 2},
                    {"questionId": "uuid-q2", "studentAnswer": "nine"},
                    {"questionId": "uuid-q5", "studentAnswer": None},
                ]
            }
        }
    }


class AttemptSaveResponseData(BaseModel):
    savedCount: int
    totalQuestions: int
    message: str = "Answers saved"


class AttemptSaveResponse(BaseModel):
    """Wrapped PATCH /api/attempts/{attempt_id}/answers response."""

    status: int = 200
    data: AttemptSaveResponseData


class AttemptAbandonResponseData(BaseModel):
    attemptId: str
    message: str = "Attempt abandoned"


class AttemptAbandonResponse(BaseModel):
    """Wrapped POST /api/attempts/{attempt_id}/abandon response."""

    status: int = 200
    data: AttemptAbandonResponseData


class AttemptSubmitRequest(BaseModel):
    """Body for POST /api/attempts/{attempt_id}/submit.

    Body answers MERGE with previously-saved ones — body overrides, saved
    answers not in body are kept and graded. `answers: []` is valid and
    grades against the saved set."""

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
    isFullyGraded: bool = True


class AttemptSubmitResponse(BaseModel):
    """Wrapped POST /api/attempts/{attempt_id}/submit response."""

    status: int = 200
    data: AttemptSubmitResponseData


class WritingCommentView(BaseModel):
    """A teacher's range-based annotation on a writing answer."""

    id: str
    rangeStart: int
    rangeEnd: int
    quotedText: str
    commentText: str
    createdBy: Optional[str] = None
    createdByName: Optional[str] = None  # teacher full_name (for display/print)
    createdAt: str
    updatedAt: str


class SpeakingCommentView(BaseModel):
    """The single overall comment a teacher attaches to a speaking answer."""

    commentText: str
    createdBy: Optional[str] = None
    createdByName: Optional[str] = None  # teacher full_name (for display/print)
    createdAt: str


class AnswerView(BaseModel):
    """Per-question breakdown returned by GET /api/attempts/{id}."""

    answerId: str
    questionId: str
    sectionId: str
    sectionPosition: int
    sectionPartLabel: Optional[str] = None
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int
    studentAnswer: Any = None
    isCorrect: Optional[bool] = None
    pointsEarned: int = 0
    # Writing-only: list of range-based teacher annotations (empty if none).
    writingComments: Optional[list[WritingCommentView]] = None
    # Speaking-only: single overall comment (null if not set).
    speakingComment: Optional[SpeakingCommentView] = None


class AttemptDetailExam(BaseModel):
    id: str
    title: str
    level: str
    skill: str


class AttemptDetailData(BaseModel):
    attempt: AttemptView
    exam: AttemptDetailExam
    answers: list[AnswerView]
    audioPlayCounts: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description=(
            "Per-attempt audio play counts. Outer key = section_id; inner key "
            "= stringified material_index. Sections absent from the outer map "
            "have no plays yet. Material indices absent from the inner map "
            "have count=0. See ATTEMPT_LIFECYCLE.md §4.6.1."
        ),
    )


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
    isAbandoned: bool = False
    isFullyGraded: bool = True
    mode: AttemptMode = "practice"
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
    materialIndex: int = Field(..., description="The audio material whose counter was incremented.")
    audioPlayCount: int = Field(..., description="Post-increment count for THIS material.")
    maxPlays: Optional[int] = Field(
        default=None,
        description="Cap value (= sections.max_audio_plays). Null = unlimited.",
    )
    remainingPlays: Optional[int] = Field(
        default=None,
        description="maxPlays - audioPlayCount, or null when unlimited.",
    )


class AudioPlayResponse(BaseModel):
    """Wrapped POST /api/attempts/{id}/sections/{sid}/audio-play response."""

    status: int = 200
    data: AudioPlayResponseData


# ---------------------------------------------------------------------------
# Speaking upload — student-side signed-URL request (WRITING_SPEAKING.md §11.2)
# ---------------------------------------------------------------------------


class SpeakingUploadRequest(BaseModel):
    """Body for POST /api/attempts/{id}/speaking-upload.

    Caller (student) supplies the question they're answering, the file's
    MIME and size. BE validates ownership + question type, then issues a
    signed PUT URL into the `student_recordings` bucket.
    """

    questionId: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    contentType: str = Field(..., min_length=1)
    fileSizeBytes: int = Field(..., ge=1)

    @model_validator(mode="after")
    def _check_mime_and_size(self):
        from services.storage_service import (
            ALLOWED_TYPES, EXT_FOR_MIME, SIZE_LIMITS,
        )
        allowed = ALLOWED_TYPES["student_recordings"]
        if self.contentType not in allowed:
            raise ValueError(
                f'Invalid contentType "{self.contentType}" for speaking upload; '
                f"allowed: {sorted(allowed)}"
            )
        if self.contentType not in EXT_FOR_MIME:
            raise ValueError(f'No extension mapping for contentType "{self.contentType}"')
        limit = SIZE_LIMITS["student_recordings"]
        if self.fileSizeBytes > limit:
            raise ValueError(
                f"File size {self.fileSizeBytes} exceeds limit of {limit} bytes"
            )
        return self


class SpeakingUploadResponseData(BaseModel):
    uploadUrl: str
    publicUrl: str
    token: str
    path: str
    bucket: str = "student_recordings"


class SpeakingUploadResponse(BaseModel):
    status: int = 200
    data: SpeakingUploadResponseData
