from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from dependencies import get_current_user
from services.attempt_service import (
    AttemptLimitExceededError,
    AudioPlayLimitExceededError,
    attempt_service,
)
from services.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from services.user_service import user_service

from .schemas import (
    ActiveAttemptData,
    ActiveAttemptResponse,
    AnswerView,
    AttemptAbandonResponse,
    AttemptAbandonResponseData,
    AttemptDetailData,
    AttemptDetailExam,
    AttemptDetailResponse,
    AttemptExamView,
    AttemptHistoryData,
    AttemptHistoryItem,
    AttemptHistoryResponse,
    AttemptSaveRequest,
    AttemptSaveResponse,
    AttemptSaveResponseData,
    AttemptStartRequest,
    AttemptStartResponse,
    AttemptStartResponseData,
    AttemptSubmitRequest,
    AttemptSubmitResponse,
    AttemptSubmitResponseData,
    AttemptView,
    AudioPlayResponse,
    AudioPlayResponseData,
    SavedAnswerView,
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
        isAbandoned=a.get("is_abandoned", False),
        startedAt=a["started_at"],
        submittedAt=a["submitted_at"],
    )


@router.post(
    "",
    response_model=AttemptStartResponse,
    # Default to 201 Created; resume path overrides to 200 OK via the
    # injected Response object (see ATTEMPT_LIFECYCLE.md §4.1).
    status_code=status.HTTP_201_CREATED,
)
async def start_attempt(
    request: AttemptStartRequest,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    """Start or resume an exam attempt (idempotent).

    Three outcomes (see ATTEMPT_LIFECYCLE.md §4.1):
      - 201 Case A — no active attempt → create new (consumes quota).
      - 200 Case B — active attempt for the SAME exam → resume.
      - 409 Case C — active attempt for a DIFFERENT exam → conflict
        (plain `{detail}` envelope; FE refreshes /active and re-decides).

    Blocked for `role=parent`. Enforces tier limit only on Case A.
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
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except AttemptLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    wire_status = (
        status.HTTP_200_OK if result["is_resume"] else status.HTTP_201_CREATED
    )
    response.status_code = wire_status

    return AttemptStartResponse(
        status=wire_status,
        data=AttemptStartResponseData(
            attemptId=result["attempt"]["id"],
            isResume=result["is_resume"],
            exam=AttemptExamView(**result["exam"]),
            savedAnswers=[SavedAnswerView(**sa) for sa in result["saved_answers"]],
            startedAt=result["attempt"]["started_at"],
        ),
    )


@router.get("/active", response_model=ActiveAttemptResponse)
async def get_active_attempt(current_user: dict = Depends(get_current_user)):
    """Return the user's single in-progress attempt (if any).

    Single source of truth for the FE's `activeAttempt` cache. Returns 404
    with `{detail: "No active attempt"}` when nothing is in progress —
    semantically correct: there is no resource to return.
    """
    user = await _resolve_user(current_user)
    active = await attempt_service.get_active_attempt(user_id=user["id"])
    if active is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No active attempt"
        )
    return ActiveAttemptResponse(data=ActiveAttemptData(**active))


@router.patch(
    "/{attempt_id}/answers", response_model=AttemptSaveResponse
)
async def save_answers(
    attempt_id: str,
    request: AttemptSaveRequest,
    current_user: dict = Depends(get_current_user),
):
    """Manual save (no grading). UPSERTs answer rows with `is_correct=NULL`.

    Idempotent — sending the same payload twice is a no-op (last write wins).
    Shape of `studentAnswer` is NOT validated here; full validation runs on
    submit. See ATTEMPT_LIFECYCLE.md §4.3.
    """
    user = await _resolve_user(current_user)
    try:
        result = await attempt_service.save_answers(
            attempt_id=attempt_id,
            user_id=user["id"],
            answers=[a.model_dump() for a in request.answers],
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AttemptSaveResponse(data=AttemptSaveResponseData(**result))


@router.post(
    "/{attempt_id}/abandon", response_model=AttemptAbandonResponse
)
async def abandon_attempt(
    attempt_id: str, current_user: dict = Depends(get_current_user)
):
    """Mark the attempt as abandoned (score=0; submitted_at=now()).

    Frees the "1 active globally" slot so the student can start a new
    attempt — but the abandoned attempt still counts toward monthly quota
    (anti-abuse). See ATTEMPT_LIFECYCLE.md §4.5.
    """
    user = await _resolve_user(current_user)
    try:
        attempt = await attempt_service.abandon_attempt(
            attempt_id=attempt_id, user_id=user["id"]
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AttemptAbandonResponse(
        data=AttemptAbandonResponseData(attemptId=attempt["id"]),
    )


@router.post(
    "/{attempt_id}/submit", response_model=AttemptSubmitResponse
)
async def submit_attempt(
    attempt_id: str,
    request: AttemptSubmitRequest,
    current_user: dict = Depends(get_current_user),
):
    """Submit + finalize. Body answers MERGE with previously-saved ones
    (body wins, saved ones not in body are kept). Owner only.

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
    materialIndex: int = Query(
        ...,
        ge=0,
        description=(
            "0-based index of the audio material within section.materials. "
            "Each audio has its own counter; cap value is shared via "
            "section.max_audio_plays."
        ),
    ),
    current_user: dict = Depends(get_current_user),
):
    """Increment the per-audio play counter for one material in this section.

    Each audio material has an independent counter; the cap value
    (`sections.max_audio_plays`) is shared but applied per-counter. Past
    the cap → 403 and the transaction rolls back (counter doesn't advance).
    """
    user = await _resolve_user(current_user)
    try:
        result = await attempt_service.record_audio_play(
            attempt_id=attempt_id,
            section_id=section_id,
            material_index=materialIndex,
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
                    isAbandoned=r.get("is_abandoned", False),
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
