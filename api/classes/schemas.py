"""Pydantic schemas for class-management endpoints.

Admin: class CRUD + teacher/student membership (/api/admin/classes).
Teacher: my-classes + class submissions (/api/teacher/classes).

See docs/class-management/class-management-design.md §5-6.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ===================================================================== #
# Admin — requests                                                       #
# ===================================================================== #


class ClassCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None


class ClassUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None


class AddTeacherRequest(BaseModel):
    teacherId: str = Field(..., min_length=1)


class AddStudentRequest(BaseModel):
    studentId: str = Field(..., min_length=1)


# ===================================================================== #
# Admin — views                                                          #
# ===================================================================== #


class ClassSummaryView(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    teacherCount: int
    studentCount: int
    createdAt: datetime


class ClassMemberView(BaseModel):
    id: str
    fullName: str
    email: str


class ClassDetailView(ClassSummaryView):
    teachers: list[ClassMemberView]
    students: list[ClassMemberView]


class ClassResponseData(BaseModel):
    class_: ClassSummaryView = Field(..., alias="class")

    model_config = {"populate_by_name": True}


class ClassResponse(BaseModel):
    status: int = 200
    data: ClassResponseData


class ClassDetailResponseData(BaseModel):
    class_: ClassDetailView = Field(..., alias="class")

    model_config = {"populate_by_name": True}


class ClassDetailResponse(BaseModel):
    status: int = 200
    data: ClassDetailResponseData


class ClassListResponseData(BaseModel):
    items: list[ClassSummaryView]


class ClassListResponse(BaseModel):
    status: int = 200
    data: ClassListResponseData


# ===================================================================== #
# Teacher — views                                                        #
# ===================================================================== #


class TeacherClassView(BaseModel):
    id: str
    name: str
    studentCount: int
    pendingGradingCount: int


class TeacherClassListResponseData(BaseModel):
    items: list[TeacherClassView]


class TeacherClassListResponse(BaseModel):
    status: int = 200
    data: TeacherClassListResponseData


class SubmissionStudentView(BaseModel):
    id: str
    fullName: str


class SubmissionExamView(BaseModel):
    id: str
    title: str
    level: str
    skill: str


class SubmissionItemView(BaseModel):
    attemptId: str
    student: SubmissionStudentView
    exam: SubmissionExamView
    submittedAt: datetime
    isFullyGraded: bool
    score: Optional[float] = None
    totalPoints: Optional[float] = None
    percentage: Optional[float] = None


class SubmissionListResponseData(BaseModel):
    items: list[SubmissionItemView]


class SubmissionListResponse(BaseModel):
    status: int = 200
    data: SubmissionListResponseData
