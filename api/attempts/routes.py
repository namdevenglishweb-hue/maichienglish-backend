from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user
from services.attempt_service import (
    AttemptLimitExceededError,
    AudioPlayLimitExceededError,
    attempt_service,
)
from services.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from services.user_service import user_service

from .schemas import (
    AnswerView,
    AttemptDetailData,
    AttemptDetailExam,
    AttemptDetailResponse,
    AttemptExamView,
    AttemptHistoryData,
    AttemptHistoryItem,
    AttemptHistoryResponse,
    AttemptStartRequest,
    AttemptStartResponse,
    AttemptStartResponseData,
    AttemptSubmitRequest,
    AttemptSubmitResponse,
    AttemptSubmitResponseData,
    AttemptView,
    AudioPlayResponse,
    AudioPlayResponseData,
)

router = APIRouter(prefix="/api/attempts", tags=["Attempts"])


async def _resolve_user(current_user: dict) -> dict:
    """Look up the full profile row of the authenticated user."""
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


def _attempt_to_view(a: dict) -> AttemptView:
    return AttemptView(
        id=a["id"],
        userId=a["user_id"],
        examId=a["exam_id"],
        score=a["score"],
        totalPoints=a["total_points"],
        percentage=a["percentage"],
        timeSpentSeconds=a["time_spent_seconds"],
        startedAt=a["started_at"],
        submittedAt=a["submitted_at"],
    )


@router.post(
    "",
    response_model=AttemptStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_attempt(
    request: AttemptStartRequest, current_user: dict = Depends(get_current_user)
):
    """Start a new exam attempt.

    - Blocked for `role=parent` (they don't take exams).
    - Enforces tier monthly attempt limit (Free 5, Basic 50, Pro/Ultra unlimited).
    - Returns the exam nested as `sections[] → questions[]` with correct
      answers stripped.
    """
    if current_user.get("role") == "parent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parents cannot start exam attempts",
        )

    user = await _resolve_user(current_user)
    try:
        result = await attempt_service.start_attempt(
            user_id=user["id"], exam_id=request.examId
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except AttemptLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    return AttemptStartResponse(
        status=201,
        data=AttemptStartResponseData(
            attemptId=result["attempt"]["id"],
            exam=AttemptExamView(**result["exam"]),
            startedAt=result["attempt"]["started_at"],
        ),
    )


@router.post(
    "/{attempt_id}/submit", response_model=AttemptSubmitResponse
)
async def submit_attempt(
    attempt_id: str,
    request: AttemptSubmitRequest,
    current_user: dict = Depends(get_current_user),
):
    """Submit answers and finalize the attempt. Owner only.

    Grading runs over every active question in every active section of the exam.
    """
    user = await _resolve_user(current_user)
    try:
        attempt = await attempt_service.submit_attempt(
            attempt_id=attempt_id,
            user_id=user["id"],
            answers=[a.model_dump() for a in request.answers],
            time_spent_seconds=request.timeSpentSeconds,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AttemptSubmitResponse(
        data=AttemptSubmitResponseData(
            score=attempt["score"],
            totalPoints=attempt["total_points"],
            percentage=attempt["percentage"],
            submittedAt=attempt["submitted_at"],
        ),
    )


@router.post(
    "/{attempt_id}/sections/{section_id}/audio-play",
    response_model=AudioPlayResponse,
)
async def record_audio_play(
    attempt_id: str,
    section_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Increment the listening-audio play counter for a section.

    Rejects past `sections.max_audio_plays`. Creates the
    `attempt_section_state` row lazily on the first call.
    """
    user = await _resolve_user(current_user)
    try:
        result = await attempt_service.record_audio_play(
            attempt_id=attempt_id,
            section_id=section_id,
            user_id=user["id"],
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        # Catches both NotOwner and AudioPlayLimitExceeded
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AudioPlayResponse(data=AudioPlayResponseData(**result))


@router.get("/history", response_model=AttemptHistoryResponse)
async def get_history(current_user: dict = Depends(get_current_user)):
    """List the current user's attempts, most recent first (capped at 100)."""
    user = await _resolve_user(current_user)
    rows = await attempt_service.list_history_for_user(user["id"])
    return AttemptHistoryResponse(
        data=AttemptHistoryData(
            items=[
                AttemptHistoryItem(
                    id=r["id"],
                    examId=r["exam_id"],
                    examTitle=r["exam_title"],
                    examLevel=r["exam_level"],
                    examSkill=r["exam_skill"],
                    score=r["score"],
                    totalPoints=r["total_points"],
                    percentage=r["percentage"],
                    timeSpentSeconds=r["time_spent_seconds"],
                    startedAt=r["started_at"],
                    submittedAt=r["submitted_at"],
                )
                for r in rows
            ]
        ),
    )


@router.get("/{attempt_id}", response_model=AttemptDetailResponse)
async def get_attempt_detail(
    attempt_id: str, current_user: dict = Depends(get_current_user)
):
    """Get an attempt with per-question breakdown (grouped by section).

    - Owner can always view.
    - Admin/teacher can view any attempt.
    - Parent can view attempts of their linked children.
    """
    detail = await attempt_service.get_attempt_with_answers(attempt_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found"
        )

    role = current_user.get("role")
    user = await _resolve_user(current_user)

    owner_id = detail["attempt"]["user_id"]
    is_owner = owner_id == user["id"]
    is_staff = role in ("admin", "teacher")
    is_parent_of_owner = False
    if role == "parent":
        is_parent_of_owner = await user_service.is_child_of(owner_id, user["id"])

    if not (is_owner or is_staff or is_parent_of_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to view this attempt",
        )

    return AttemptDetailResponse(
        data=AttemptDetailData(
            attempt=_attempt_to_view(detail["attempt"]),
            exam=AttemptDetailExam(**detail["exam"]),
            answers=[
                AnswerView(
                    answerId=a["answer_id"],
                    questionId=a["question_id"],
                    sectionId=a["section_id"],
                    sectionPosition=a["section_position"],
                    sectionPartLabel=a["section_part_label"],
                    position=a["position"],
                    questionType=a["question_type"],
                    questionData=a["question_data"],
                    points=a["points"],
                    studentAnswer=a["student_answer"],
                    isCorrect=a["is_correct"],
                    pointsEarned=a["points_earned"],
                )
                for a in detail["answers"]
            ],
        ),
    )
