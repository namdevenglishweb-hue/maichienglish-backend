from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user, require_admin
from services.exam_service import exam_service
from services.exceptions import NotFoundError, ValidationError
from services.question_service import question_service
from services.section_service import section_service

from .schemas import (
    QuestionCreate,
    QuestionListResponse,
    QuestionListResponseData,
    QuestionResponse,
    QuestionResponseData,
    QuestionUpdate,
    QuestionView,
)

# Mounted under two prefixes:
#   section_scoped_router: list/create scoped to a section
#   question_router:       id-scoped read/update/delete
section_scoped_router = APIRouter(prefix="/api/sections", tags=["Questions"])
question_router = APIRouter(prefix="/api/questions", tags=["Questions"])


def _to_view(q: dict) -> QuestionView:
    return QuestionView(
        id=q["id"],
        sectionId=q["section_id"],
        position=q["position"],
        questionType=q["question_type"],
        questionData=q["question_data"],
        points=q["points"],
        createdAt=q["created_at"],
        deletedAt=q["deleted_at"],
    )


def _is_privileged(role: str | None) -> bool:
    return role in ("admin", "teacher")


@section_scoped_router.get(
    "/{section_id}/questions", response_model=QuestionListResponse,
)
async def list_questions(
    section_id: str, current_user: dict = Depends(get_current_user)
):
    """List questions of a section. Non-privileged users only see questions of published exams."""
    section = await section_service.get_section(section_id)
    if not section:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
        )

    if not _is_privileged(current_user.get("role")):
        exam = await exam_service.get_exam(section["exam_id"])
        if not exam or not exam["is_published"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
            )

    questions = await question_service.list_questions_by_section(section_id)
    return QuestionListResponse(
        data=QuestionListResponseData(items=[_to_view(q) for q in questions]),
    )


@section_scoped_router.post(
    "/{section_id}/questions",
    response_model=QuestionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_question(section_id: str, request: QuestionCreate):
    """Add a question to a section (admin only). Position auto-assigned if omitted."""
    try:
        question = await question_service.create_question(
            section_id=section_id,
            question_type=request.question_type,
            question_data=request.question_data,
            points=request.points,
            position=request.position,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return QuestionResponse(
        status=201, data=QuestionResponseData(question=_to_view(question))
    )


@question_router.get("/{question_id}", response_model=QuestionResponse)
async def get_question(
    question_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a single question. Non-privileged users only see questions of published exams."""
    question = await question_service.get_question(question_id)
    if not question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Question not found"
        )
    if not _is_privileged(current_user.get("role")):
        exam_id = await question_service.get_exam_id_for_question(question_id)
        exam = await exam_service.get_exam(exam_id) if exam_id else None
        if not exam or not exam["is_published"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Question not found"
            )
    return QuestionResponse(data=QuestionResponseData(question=_to_view(question)))


@question_router.put(
    "/{question_id}",
    response_model=QuestionResponse,
    dependencies=[Depends(require_admin)],
)
async def update_question(question_id: str, request: QuestionUpdate):
    """Update a question (admin only). Pass only the fields you want to change."""
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )
    try:
        question = await question_service.update_question(question_id, **updates)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return QuestionResponse(data=QuestionResponseData(question=_to_view(question)))


@question_router.delete(
    "/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def soft_delete_question(question_id: str):
    """Soft-delete a question (admin only). Sets deleted_at; data preserved."""
    try:
        await question_service.soft_delete_question(question_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return None


@question_router.delete(
    "/{question_id}/hard",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def hard_delete_question(question_id: str):
    """Hard-delete a question (admin only). CASCADEs through answers."""
    try:
        await question_service.hard_delete_question(question_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return None


__all__ = ["section_scoped_router", "question_router"]
