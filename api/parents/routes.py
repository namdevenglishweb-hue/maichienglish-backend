from fastapi import APIRouter, Depends, HTTPException, status

from api.attempts.schemas import (
    AnswerView,
    AttemptDetailData,
    AttemptDetailExam,
    AttemptDetailResponse,
    AttemptHistoryData,
    AttemptHistoryItem,
    AttemptHistoryResponse,
    AttemptView,
)
from dependencies import require_parent
from services.attempt_service import attempt_service
from services.user_service import user_service

from .schemas import ChildrenListData, ChildrenListResponse, ChildView

router = APIRouter(
    prefix="/api/parents/me",
    tags=["Parents"],
    dependencies=[Depends(require_parent)],
)


async def _resolve_parent(parent_claims: dict) -> dict:
    parent = await user_service.get_by_email(parent_claims["sub"])
    if not parent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent profile not found"
        )
    return parent


async def _assert_owns_child(parent_id: str, student_id: str) -> None:
    if not await user_service.is_child_of(student_id, parent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a linked child of this parent",
        )


@router.get("/children", response_model=ChildrenListResponse)
async def list_my_children(current_parent: dict = Depends(require_parent)):
    """List students linked to the current parent via profiles.parent_id."""
    parent = await _resolve_parent(current_parent)
    children = await user_service.list_children_of_parent(parent["id"])
    return ChildrenListResponse(
        data=ChildrenListData(
            children=[
                ChildView(
                    id=c["id"],
                    email=c["email"],
                    fullName=c["full_name"],
                    phone=c["phone"],
                    createdAt=c["created_at"],
                )
                for c in children
            ],
        ),
    )


@router.get(
    "/children/{student_id}/attempts", response_model=AttemptHistoryResponse
)
async def list_child_attempts(
    student_id: str, current_parent: dict = Depends(require_parent)
):
    """List a linked child's attempt history (most recent first, capped at 100)."""
    parent = await _resolve_parent(current_parent)
    await _assert_owns_child(parent["id"], student_id)

    rows = await attempt_service.list_history_for_user(student_id)
    return AttemptHistoryResponse(
        data=AttemptHistoryData(
            attempts=[
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


@router.get(
    "/children/{student_id}/attempts/{attempt_id}",
    response_model=AttemptDetailResponse,
)
async def get_child_attempt_detail(
    student_id: str,
    attempt_id: str,
    current_parent: dict = Depends(require_parent),
):
    """Get a linked child's specific attempt with per-question breakdown."""
    parent = await _resolve_parent(current_parent)
    await _assert_owns_child(parent["id"], student_id)

    detail = await attempt_service.get_attempt_with_answers(attempt_id)
    if not detail or detail["attempt"]["user_id"] != student_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found"
        )

    a = detail["attempt"]
    return AttemptDetailResponse(
        data=AttemptDetailData(
            attempt=AttemptView(
                id=a["id"],
                userId=a["user_id"],
                examId=a["exam_id"],
                score=a["score"],
                totalPoints=a["total_points"],
                percentage=a["percentage"],
                timeSpentSeconds=a["time_spent_seconds"],
                audioPlayCount=a["audio_play_count"],
                startedAt=a["started_at"],
                submittedAt=a["submitted_at"],
            ),
            exam=AttemptDetailExam(**detail["exam"]),
            answers=[
                AnswerView(
                    answerId=ans["answer_id"],
                    questionId=ans["question_id"],
                    position=ans["position"],
                    questionType=ans["question_type"],
                    questionData=ans["question_data"],
                    points=ans["points"],
                    studentAnswer=ans["student_answer"],
                    isCorrect=ans["is_correct"],
                    pointsEarned=ans["points_earned"],
                )
                for ans in detail["answers"]
            ],
        ),
    )
