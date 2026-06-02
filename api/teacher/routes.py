"""Teacher-only endpoints — manual grading + writing/speaking comments.

All routes mounted under /api/teacher/ with router-level
`require_teacher_or_admin` dependency (teachers + admins can use them;
students + parents get 403).

See WRITING_SPEAKING.md §8 (grading) and §9 (comments).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from dependencies import require_teacher_or_admin
from services.comment_service import comment_service
from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from services.grading_service import grading_service
from services.user_service import user_service

from .schemas import (
    GradeRequest,
    GradeResponse,
    GradeResponseData,
    SpeakingCommentData,
    SpeakingCommentResponse,
    SpeakingCommentUpdateRequest,
    WritingCommentCreateRequest,
    WritingCommentData,
    WritingCommentResponse,
    WritingCommentUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/teacher",
    tags=["Teacher"],
    dependencies=[Depends(require_teacher_or_admin)],
)


async def _resolve_teacher(current_user: dict) -> dict:
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Teacher profile not found"
        )
    return user


# =========================================================================
# Grading
# =========================================================================


@router.post("/attempts/{attempt_id}/grade", response_model=GradeResponse)
async def grade_attempt(
    attempt_id: str,
    request: GradeRequest,
    current_user: dict = Depends(require_teacher_or_admin),
):
    """Grade one or more writing/speaking questions on an attempt.

    Partial grading supported — `isFullyGraded` only flips true when the
    LAST manual question gets a score. When the flag transitions, an
    E5 email is scheduled to the student (currently logged TODO; will be
    wired up when EMAIL.md infrastructure ships).
    """
    grades = [g.model_dump() for g in request.grades]
    try:
        result = await grading_service.grade_attempt(
            attempt_id=attempt_id, grades=grades,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # E5 email hook — fires when is_fully_graded just flipped to true.
    if result.get("student_email"):
        # TODO: wire to email_service.schedule_email(...) once EMAIL.md ships.
        # For now, log the intent so ops can verify the trigger fires.
        logger.info(
            "E5 student-graded email NOT YET SENT (email infra pending) — "
            "attempt=%s recipient=%s",
            attempt_id, result["student_email"],
        )

    return GradeResponse(
        data=GradeResponseData(
            graded=result["graded"],
            attemptScore=result["attempt_score"],
            attemptTotalPoints=result["attempt_total_points"],
            attemptPercentage=result["attempt_percentage"],
            isFullyGraded=result["is_fully_graded"],
        )
    )


# =========================================================================
# Writing range comments — POST / PUT / DELETE
# =========================================================================


@router.post(
    "/attempts/{attempt_id}/answers/{answer_id}/writing-comments",
    response_model=WritingCommentResponse,
    status_code=201,
)
async def create_writing_comment(
    attempt_id: str,
    answer_id: str,
    request: WritingCommentCreateRequest,
    current_user: dict = Depends(require_teacher_or_admin),
):
    teacher = await _resolve_teacher(current_user)
    try:
        c = await comment_service.create_writing_comment(
            attempt_id=attempt_id,
            answer_id=answer_id,
            teacher_id=teacher["id"],
            range_start=request.rangeStart,
            range_end=request.rangeEnd,
            quoted_text=request.quotedText,
            comment_text=request.commentText,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return WritingCommentResponse(
        status=201,
        data=WritingCommentData(
            id=c["id"],
            rangeStart=c["range_start"],
            rangeEnd=c["range_end"],
            quotedText=c["quoted_text"],
            commentText=c["comment_text"],
            createdBy=c["created_by"],
            createdAt=c["created_at"],
            updatedAt=c["updated_at"],
        ),
    )


@router.put(
    "/attempts/{attempt_id}/answers/{answer_id}/writing-comments/{comment_id}",
    response_model=WritingCommentResponse,
)
async def update_writing_comment(
    attempt_id: str,
    answer_id: str,
    comment_id: str,
    request: WritingCommentUpdateRequest,
    current_user: dict = Depends(require_teacher_or_admin),
):
    """Edit comment text (range immutable — DELETE + POST to change range)."""
    teacher = await _resolve_teacher(current_user)
    try:
        c = await comment_service.update_writing_comment(
            attempt_id=attempt_id,
            answer_id=answer_id,
            comment_id=comment_id,
            teacher_id=teacher["id"],
            comment_text=request.commentText,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return WritingCommentResponse(
        data=WritingCommentData(
            id=c["id"],
            rangeStart=c["range_start"],
            rangeEnd=c["range_end"],
            quotedText=c["quoted_text"],
            commentText=c["comment_text"],
            createdBy=c["created_by"],
            createdAt=c["created_at"],
            updatedAt=c["updated_at"],
        ),
    )


@router.delete(
    "/attempts/{attempt_id}/answers/{answer_id}/writing-comments/{comment_id}",
    status_code=204,
    response_class=Response,
)
async def delete_writing_comment(
    attempt_id: str,
    answer_id: str,
    comment_id: str,
    current_user: dict = Depends(require_teacher_or_admin),
):
    try:
        await comment_service.delete_writing_comment(
            attempt_id=attempt_id,
            answer_id=answer_id,
            comment_id=comment_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =========================================================================
# Speaking overall comment — PUT (UPSERT) / DELETE
# =========================================================================


@router.put(
    "/attempts/{attempt_id}/answers/{answer_id}/speaking-comment",
    response_model=SpeakingCommentResponse,
)
async def upsert_speaking_comment(
    attempt_id: str,
    answer_id: str,
    request: SpeakingCommentUpdateRequest,
    current_user: dict = Depends(require_teacher_or_admin),
):
    """UPSERT the single overall comment on a speaking answer.

    Overwrites any existing comment (matches "1 đoạn comment duy nhất" rule).
    """
    teacher = await _resolve_teacher(current_user)
    try:
        c = await comment_service.upsert_speaking_comment(
            attempt_id=attempt_id,
            answer_id=answer_id,
            teacher_id=teacher["id"],
            comment_text=request.commentText,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return SpeakingCommentResponse(
        data=SpeakingCommentData(
            commentText=c["comment_text"],
            createdBy=c["created_by"],
            createdAt=c["created_at"],
        ),
    )


@router.delete(
    "/attempts/{attempt_id}/answers/{answer_id}/speaking-comment",
    status_code=204,
    response_class=Response,
)
async def delete_speaking_comment(
    attempt_id: str,
    answer_id: str,
    current_user: dict = Depends(require_teacher_or_admin),
):
    try:
        await comment_service.delete_speaking_comment(
            attempt_id=attempt_id, answer_id=answer_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
