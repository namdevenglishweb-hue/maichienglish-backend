"""Class-management HTTP routes.

Two routers live in this one file (design §6 — keep all class code in one
place rather than splitting into the teacher-grading package):

  - admin_router   prefix /api/admin/classes  (require_admin)
        class CRUD + teacher/student membership
  - teacher_router prefix /api/teacher         (require_teacher_or_admin)
        GET /classes  +  GET /classes/{id}/submissions

Authorization scoping for the teacher submissions endpoint is enforced
here at the route layer (teacher_teaches_class; admin bypass). The RBAC
amendments to grade/comment/attempt-detail are a separate phase.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from dependencies import require_admin, require_teacher_or_admin
from services.class_service import class_service
from services.exceptions import (
    AlreadyExistsError,
    NotFoundError,
    ValidationError,
)
from services.user_service import user_service

from .schemas import (
    AddStudentRequest,
    AddTeacherRequest,
    ClassCreateRequest,
    ClassDetailResponse,
    ClassDetailResponseData,
    ClassDetailView,
    ClassListResponse,
    ClassListResponseData,
    ClassMemberView,
    ClassResponse,
    ClassResponseData,
    ClassSummaryView,
    ClassUpdateRequest,
    SubmissionExamView,
    SubmissionItemView,
    SubmissionListResponse,
    SubmissionListResponseData,
    SubmissionStudentView,
    TeacherClassListResponse,
    TeacherClassListResponseData,
    TeacherClassView,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/classes",
    tags=["Admin · Classes"],
    dependencies=[Depends(require_admin)],
)

teacher_router = APIRouter(
    prefix="/api/teacher",
    tags=["Teacher · Classes"],
    dependencies=[Depends(require_teacher_or_admin)],
)


# --------------------------------------------------------------------- #
# Mappers                                                                #
# --------------------------------------------------------------------- #


def _summary_view(c: dict) -> ClassSummaryView:
    return ClassSummaryView(
        id=c["id"],
        name=c["name"],
        description=c["description"],
        teacherCount=c["teacher_count"],
        studentCount=c["student_count"],
        createdAt=c["created_at"],
    )


def _detail_view(c: dict) -> ClassDetailView:
    return ClassDetailView(
        id=c["id"],
        name=c["name"],
        description=c["description"],
        teacherCount=c["teacher_count"],
        studentCount=c["student_count"],
        createdAt=c["created_at"],
        teachers=[
            ClassMemberView(id=m["id"], fullName=m["full_name"], email=m["email"])
            for m in c["teachers"]
        ],
        students=[
            ClassMemberView(id=m["id"], fullName=m["full_name"], email=m["email"])
            for m in c["students"]
        ],
    )


async def _resolve_user_id(current_user: dict) -> str:
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    return user["id"]


# ===================================================================== #
# Admin — class CRUD                                                     #
# ===================================================================== #


@admin_router.post("", response_model=ClassResponse, status_code=201)
async def create_class(request: ClassCreateRequest):
    c = await class_service.create_class(
        name=request.name, description=request.description
    )
    return ClassResponse(
        status=201, data=ClassResponseData(**{"class": _summary_view(c)})
    )


@admin_router.get("", response_model=ClassListResponse)
async def list_classes():
    items = await class_service.list_classes()
    return ClassListResponse(
        data=ClassListResponseData(items=[_summary_view(c) for c in items])
    )


@admin_router.get("/{class_id}", response_model=ClassDetailResponse)
async def get_class(class_id: str):
    try:
        c = await class_service.get_class(class_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ClassDetailResponse(
        data=ClassDetailResponseData(**{"class": _detail_view(c)})
    )


@admin_router.patch("/{class_id}", response_model=ClassResponse)
async def update_class(class_id: str, request: ClassUpdateRequest):
    description_set = "description" in request.model_fields_set
    try:
        c = await class_service.update_class(
            class_id=class_id,
            name=request.name,
            description=request.description,
            description_set=description_set,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ClassResponse(data=ClassResponseData(**{"class": _summary_view(c)}))


@admin_router.delete("/{class_id}", status_code=204, response_class=Response)
async def delete_class(class_id: str):
    try:
        await class_service.delete_class(class_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ===================================================================== #
# Admin — teacher membership                                            #
# ===================================================================== #


@admin_router.post(
    "/{class_id}/teachers", response_model=ClassDetailResponse, status_code=201
)
async def add_teacher(class_id: str, request: AddTeacherRequest):
    try:
        await class_service.add_teacher(class_id, request.teacherId)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except AlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    c = await class_service.get_class(class_id)
    return ClassDetailResponse(
        status=201, data=ClassDetailResponseData(**{"class": _detail_view(c)})
    )


@admin_router.delete(
    "/{class_id}/teachers/{teacher_id}", status_code=204, response_class=Response
)
async def remove_teacher(class_id: str, teacher_id: str):
    try:
        await class_service.remove_teacher(class_id, teacher_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ===================================================================== #
# Admin — student membership                                            #
# ===================================================================== #


@admin_router.post(
    "/{class_id}/students", response_model=ClassDetailResponse, status_code=201
)
async def add_student(class_id: str, request: AddStudentRequest):
    try:
        await class_service.add_student(class_id, request.studentId)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except AlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    c = await class_service.get_class(class_id)
    return ClassDetailResponse(
        status=201, data=ClassDetailResponseData(**{"class": _detail_view(c)})
    )


@admin_router.delete(
    "/{class_id}/students/{student_id}", status_code=204, response_class=Response
)
async def remove_student(class_id: str, student_id: str):
    try:
        await class_service.remove_student(class_id, student_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ===================================================================== #
# Teacher — my classes + submissions                                    #
# ===================================================================== #


@teacher_router.get("/classes", response_model=TeacherClassListResponse)
async def list_my_classes(current_user: dict = Depends(require_teacher_or_admin)):
    """Classes the caller teaches (admin sees all), each with studentCount
    + pendingGradingCount."""
    if current_user.get("role") == "admin":
        items = await class_service.list_teacher_classes(teacher_id=None)
    else:
        teacher_id = await _resolve_user_id(current_user)
        items = await class_service.list_teacher_classes(teacher_id=teacher_id)
    return TeacherClassListResponse(
        data=TeacherClassListResponseData(
            items=[
                TeacherClassView(
                    id=c["id"],
                    name=c["name"],
                    studentCount=c["student_count"],
                    pendingGradingCount=c["pending_grading_count"],
                )
                for c in items
            ]
        )
    )


@teacher_router.get(
    "/classes/{class_id}/submissions", response_model=SubmissionListResponse
)
async def list_class_submissions(
    class_id: str,
    status_filter: Literal["pending", "all"] = Query(default="all", alias="status"),
    current_user: dict = Depends(require_teacher_or_admin),
):
    """Submitted (non-abandoned) attempts of the class's students.

    Authorization: admin bypasses; a teacher must teach the class
    (`teacher_teaches_class`) else 403. Class-not-found → 404 (checked
    before the teaching check so a missing class never masquerades as 403).
    """
    if current_user.get("role") != "admin":
        teacher_id = await _resolve_user_id(current_user)
        teaches = await class_service.teacher_teaches_class(teacher_id, class_id)
        if not teaches:
            if not await class_service.class_exists(class_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Class {class_id} not found",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not teach this class",
            )

    try:
        subs = await class_service.list_class_submissions(
            class_id, status=status_filter
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return SubmissionListResponse(
        data=SubmissionListResponseData(
            items=[
                SubmissionItemView(
                    attemptId=s["attempt_id"],
                    student=SubmissionStudentView(
                        id=s["student"]["id"], fullName=s["student"]["full_name"]
                    ),
                    exam=SubmissionExamView(
                        id=s["exam"]["id"],
                        title=s["exam"]["title"],
                        level=s["exam"]["level"],
                        skill=s["exam"]["skill"],
                    ),
                    submittedAt=s["submitted_at"],
                    isFullyGraded=s["is_fully_graded"],
                    score=s["score"],
                    totalPoints=s["total_points"],
                    percentage=s["percentage"],
                )
                for s in subs
            ]
        )
    )
