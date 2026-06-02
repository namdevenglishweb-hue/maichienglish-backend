"""Pydantic schemas for /api/teacher/ endpoints — manual grading + comments.

See WRITING_SPEAKING.md §8 (grading) and §9 (comments).
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Grading — POST /api/teacher/attempts/{id}/grade
# ---------------------------------------------------------------------------


class GradeItem(BaseModel):
    questionId: str = Field(..., min_length=1)
    pointsEarned: int = Field(..., ge=0)


class GradeRequest(BaseModel):
    grades: list[GradeItem] = Field(..., min_length=1)


class GradeResponseData(BaseModel):
    graded: int
    attemptScore: float
    attemptTotalPoints: float
    attemptPercentage: float
    isFullyGraded: bool


class GradeResponse(BaseModel):
    status: int = 200
    data: GradeResponseData


# ---------------------------------------------------------------------------
# Writing comments — range-based annotations on writing answers
# ---------------------------------------------------------------------------


class WritingCommentCreateRequest(BaseModel):
    rangeStart: int = Field(..., ge=0)
    rangeEnd: int = Field(..., gt=0)
    quotedText: str = Field(..., min_length=1)
    commentText: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_range(self):
        if self.rangeEnd <= self.rangeStart:
            raise ValueError("rangeEnd must be greater than rangeStart")
        return self


class WritingCommentUpdateRequest(BaseModel):
    commentText: str = Field(..., min_length=1)


class WritingCommentData(BaseModel):
    id: str
    rangeStart: int
    rangeEnd: int
    quotedText: str
    commentText: str
    createdBy: Optional[str] = None
    createdAt: str
    updatedAt: str


class WritingCommentResponse(BaseModel):
    """Response for POST / PUT writing-comments endpoints."""

    status: int = 200
    data: WritingCommentData


# ---------------------------------------------------------------------------
# Speaking comment — single overall comment per speaking answer
# ---------------------------------------------------------------------------


class SpeakingCommentUpdateRequest(BaseModel):
    commentText: str = Field(..., min_length=1)


class SpeakingCommentData(BaseModel):
    commentText: str
    createdBy: Optional[str] = None
    createdAt: str


class SpeakingCommentResponse(BaseModel):
    """Response for PUT speaking-comment endpoint."""

    status: int = 200
    data: SpeakingCommentData
