from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from dependencies import get_current_user, require_admin, require_teacher_or_admin
from services.exam_service import exam_service
from services.exceptions import NotFoundError, ValidationError
from services.question_service import question_service
from services.section_service import section_service
from services.user_service import user_service
from utils.grading_utils import strip_correct, strip_material_meta

from .schemas import (
    ExamCreate,
    ExamListResponse,
    ExamListResponseData,
    ExamQuestionPreview,
    ExamResponse,
    ExamResponseData,
    ExamScaffoldRequest,
    ExamSectionPreview,
    ExamUpdate,
    ExamView,
)

router = APIRouter(prefix="/api/exams", tags=["Exams"])


def _to_view(exam: dict, *, include_meta: bool = True) -> ExamView:
    # generationMeta is admin/teacher-only audit; students see the badge id
    # (generatedFromExamId) but not the meta. include_meta defaults True so
    # admin-only write paths keep it; read paths pass include_meta=is_priv.
    return ExamView(
        id=exam["id"],
        title=exam["title"],
        level=exam["level"],
        skill=exam["skill"],
        durationMinutes=exam["duration_minutes"],
        description=exam["description"],
        isPublished=exam["is_published"],
        createdBy=exam["created_by"],
        createdAt=exam["created_at"],
        updatedAt=exam["updated_at"],
        deletedAt=exam["deleted_at"],
        generatedFromExamId=exam.get("generated_from_exam_id"),
        generationMeta=exam.get("generation_meta") if include_meta else None,
        formatStandard=exam.get("format_standard"),
    )


def _is_privileged(role: Optional[str]) -> bool:
    return role in ("admin", "teacher")


async def _build_sections_payload(
    exam_id: str, is_priv: bool
) -> list[ExamSectionPreview]:
    """Build `sections[]` with embedded `questions[]` for ?include=sections.

    Strips correct-answer fields from each question_data when `is_priv` is
    False, so the same builder serves both admin and student callers.
    """
    sections = await section_service.list_sections_by_exam(exam_id)
    out: list[ExamSectionPreview] = []
    for s in sections:
        questions = await question_service.list_questions_by_section(s["id"])
        out.append(
            ExamSectionPreview(
                id=s["id"],
                position=s["position"],
                partLabel=s["part_label"],
                type=s["type"],
                instructions=s["instructions"],
                # Admin/teacher see material.meta (transcript/description);
                # students don't (it leaks listening/image answers — §5.4).
                materials=s["materials"]
                if is_priv
                else strip_material_meta(s["materials"]),
                maxAudioPlays=s["max_audio_plays"],
                partCode=s.get("part_code"),
                questions=[
                    ExamQuestionPreview(
                        id=q["id"],
                        position=q["position"],
                        questionType=q["question_type"],
                        questionData=q["question_data"]
                        if is_priv
                        else strip_correct(q["question_type"], q["question_data"]),
                        points=q["points"],
                    )
                    for q in questions
                ],
            )
        )
    return out


@router.get("", response_model=ExamListResponse)
async def list_exams(
    level: Optional[str] = None,
    skill: Optional[str] = None,
    published: Optional[bool] = None,
    current_user: dict = Depends(get_current_user),
):
    """List exams. Students see only published exams; admins/teachers can filter freely."""
    role = current_user.get("role")
    if not _is_privileged(role):
        published = True

    exams = await exam_service.list_exams(
        level=level, skill=skill, is_published=published
    )
    return ExamListResponse(
        data=ExamListResponseData(
            items=[_to_view(e, include_meta=_is_privileged(role)) for e in exams]
        ),
    )


@router.get("/{exam_id}", response_model=ExamResponse)
async def get_exam(
    exam_id: str,
    include: Optional[str] = Query(
        default=None,
        description="Comma-separated includes. Pass `sections` to nest the section→question tree.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Get a single exam by id. Non-privileged users only see published exams.

    Use `?include=sections` to embed `sections[] → questions[]` under
    `data.exam.sections`. For non-privileged users, correct-answer fields are
    stripped from each question.
    """
    exam = await exam_service.get_exam(exam_id)
    if not exam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found"
        )
    is_priv = _is_privileged(current_user.get("role"))
    if not is_priv and not exam["is_published"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found"
        )

    view = _to_view(exam, include_meta=is_priv)

    includes = {p.strip() for p in (include or "").split(",") if p.strip()}
    if "sections" in includes:
        view.sections = await _build_sections_payload(exam_id, is_priv)

    return ExamResponse(data=ExamResponseData(exam=view))


@router.post(
    "",
    response_model=ExamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exam(
    request: ExamCreate, admin: dict = Depends(require_admin)
):
    """Create a new exam (admin only).

    Plain mode: returns just the exam metadata. Add sections separately
    via §4.5.

    Nested mode: if `sections` is supplied (each may also nest
    `questions`), the whole tree is created in one transaction.
    Server-assigned positions 1..N in array order. Gap markers inside each
    section's materials are validated against that section's question
    positions before any INSERT runs. Response includes `createdCounts`
    so the FE can confirm what was persisted; call
    `GET /api/exams/{id}?include=sections` to fetch the IDs.
    """
    admin_profile = await user_service.get_by_email(admin["sub"])
    created_by = admin_profile["id"] if admin_profile else None

    if request.sections is not None:
        try:
            exam = await exam_service.create_exam_nested(
                title=request.title,
                level=request.level,
                skill=request.skill,
                duration_minutes=request.duration_minutes,
                description=request.description,
                created_by=created_by,
                sections=[s.model_dump(exclude_unset=False) for s in request.sections],
            )
        except ValidationError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return ExamResponse(
            status=201,
            data=ExamResponseData(
                exam=_to_view(exam),
                createdCounts=exam.get("created_counts"),
            ),
        )

    exam = await exam_service.create_exam(
        title=request.title,
        level=request.level,
        skill=request.skill,
        duration_minutes=request.duration_minutes,
        description=request.description,
        created_by=created_by,
    )
    return ExamResponse(status=201, data=ExamResponseData(exam=_to_view(exam)))


@router.post(
    "/scaffold",
    response_model=ExamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def scaffold_exam(
    request: ExamScaffoldRequest,
    current_user: dict = Depends(require_teacher_or_admin),
):
    """B4 — create an UNPUBLISHED exam pre-filled with every Cambridge Part for
    (level, skill), each section scaffolded empty-but-valid from its preset
    (docs/exam-part-presets §4). Reuses create_exam_nested. `skill` must be
    reading|listening (Writing/Speaking are single Parts, not exams) → else 400.
    Call GET /api/exams/{id}?include=sections to fetch the section IDs.
    """
    profile = await user_service.get_by_email(current_user["sub"])
    created_by = profile["id"] if profile else None
    try:
        exam = await exam_service.scaffold_exam(
            request.level, request.skill,
            format_standard=request.formatStandard or "cambridge_2020",
            title=request.title, created_by=created_by,
        )
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ExamResponse(
        status=201,
        data=ExamResponseData(exam=_to_view(exam), createdCounts=exam.get("created_counts")),
    )


@router.put(
    "/{exam_id}",
    response_model=ExamResponse,
    dependencies=[Depends(require_admin)],
)
async def update_exam(exam_id: str, request: ExamUpdate):
    """Update an exam (admin only). Pass only the fields you want to change."""
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )
    try:
        exam = await exam_service.update_exam(exam_id, **updates)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ExamResponse(data=ExamResponseData(exam=_to_view(exam)))


@router.post(
    "/{exam_id}/publish",
    response_model=ExamResponse,
    dependencies=[Depends(require_admin)],
)
async def publish_exam(exam_id: str):
    """Publish an exam (admin only). Requires ≥1 section with ≥1 active question."""
    try:
        exam = await exam_service.publish_exam(exam_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ExamResponse(data=ExamResponseData(exam=_to_view(exam)))


@router.post(
    "/{exam_id}/unpublish",
    response_model=ExamResponse,
    dependencies=[Depends(require_admin)],
)
async def unpublish_exam(exam_id: str):
    """Unpublish an exam (admin only). Hides it from students without deleting."""
    try:
        exam = await exam_service.unpublish_exam(exam_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ExamResponse(data=ExamResponseData(exam=_to_view(exam)))


@router.delete(
    "/{exam_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def soft_delete_exam(exam_id: str):
    """Soft-delete an exam (admin only). Sets deleted_at; data preserved."""
    try:
        await exam_service.soft_delete_exam(exam_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return None


@router.delete(
    "/{exam_id}/hard",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def hard_delete_exam(exam_id: str):
    """Hard-delete an exam (admin only). CASCADEs through sections/questions/attempts/answers."""
    try:
        await exam_service.hard_delete_exam(exam_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return None
