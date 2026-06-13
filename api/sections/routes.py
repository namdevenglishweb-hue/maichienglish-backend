from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from dependencies import get_current_user, require_admin
from services.exam_service import exam_service
from services.exceptions import NotFoundError, ValidationError
from services.section_service import section_service
from utils.grading_utils import strip_material_meta

from api.common import BatchDeleteRequest

from .schemas import (
    SectionBatchUpdateRequest,
    SectionBatchUpdateResponse,
    SectionCreate,
    SectionListResponse,
    SectionListResponseData,
    SectionQuestionPreview,
    SectionResponse,
    SectionResponseData,
    SectionUpdate,
    SectionView,
)

# Mounted under two prefixes: exam-scoped list/create vs id-scoped read/update/delete.
exam_scoped_router = APIRouter(prefix="/api/exams", tags=["Sections"])
section_router = APIRouter(prefix="/api/sections", tags=["Sections"])


def _to_view(s: dict, *, strip_meta: bool = False) -> SectionView:
    # strip_meta=True for student-facing reads: removes admin-only
    # material.meta (transcript/description). Admin CRUD keeps it. See §5.4.
    materials = strip_material_meta(s["materials"]) if strip_meta else s["materials"]
    return SectionView(
        id=s["id"],
        examId=s["exam_id"],
        position=s["position"],
        partLabel=s["part_label"],
        type=s["type"],
        instructions=s["instructions"],
        materials=materials,
        maxAudioPlays=s["max_audio_plays"],
        createdAt=s["created_at"],
        updatedAt=s["updated_at"],
        deletedAt=s["deleted_at"],
    )


def _is_privileged(role: Optional[str]) -> bool:
    return role in ("admin", "teacher")


@exam_scoped_router.get(
    "/{exam_id}/sections", response_model=SectionListResponse
)
async def list_sections(
    exam_id: str, current_user: dict = Depends(get_current_user)
):
    """List sections of an exam. Non-privileged users only see sections of published exams."""
    exam = await exam_service.get_exam(exam_id)
    if not exam:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found"
        )
    if not _is_privileged(current_user.get("role")) and not exam["is_published"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found"
        )

    strip_meta = not _is_privileged(current_user.get("role"))
    sections = await section_service.list_sections_by_exam(exam_id)
    return SectionListResponse(
        data=SectionListResponseData(
            items=[_to_view(s, strip_meta=strip_meta) for s in sections]
        ),
    )


@exam_scoped_router.post(
    "/{exam_id}/sections",
    response_model=SectionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_section(exam_id: str, request: SectionCreate):
    """Create a new section under an exam (admin only).

    If `questions` is supplied, both the section and all questions are
    created in one transaction; gap markers in `materials` are validated
    against the resulting question positions (1..N in array order). Reject
    rolls back the whole batch.
    """
    materials = [m.model_dump(exclude_none=True) for m in request.materials]
    nested_questions = (
        [q.model_dump(exclude_unset=False) for q in request.questions]
        if request.questions is not None
        else None
    )

    try:
        if nested_questions is not None:
            section = await section_service.create_section_with_questions(
                exam_id=exam_id,
                part_label=request.partLabel,
                type=request.type,
                instructions=request.instructions,
                materials=materials,
                max_audio_plays=request.maxAudioPlays,
                position=request.position,
                questions=nested_questions,
                part_code=request.partCode,
            )
        else:
            section = await section_service.create_section(
                exam_id=exam_id,
                part_label=request.partLabel,
                type=request.type,
                instructions=request.instructions,
                materials=materials,
                max_audio_plays=request.maxAudioPlays,
                position=request.position,
                part_code=request.partCode,
            )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SectionResponse(
        status=201,
        data=SectionResponseData(
            section=_to_view(section),
            createdCounts=section.get("created_counts"),
        ),
    )


# ---------------------------------------------------------------------------
# Batch endpoints — MUST be registered before the `/{section_id}` routes
# below, otherwise FastAPI matches `/batch` as section_id="batch".
# ---------------------------------------------------------------------------


@section_router.put(
    "/batch",
    response_model=SectionBatchUpdateResponse,
    dependencies=[Depends(require_admin)],
)
async def batch_update_sections(request: SectionBatchUpdateRequest):
    """Update up to 100 sections in one transaction (admin only).

    Each item must include `id`; remaining fields follow `SectionUpdate`
    semantics. Any invalid item or missing id rolls back the whole batch.
    """
    field_map = {
        "partLabel": "part_label",
        "type": "type",
        "instructions": "instructions",
        "materials": "materials",
        "maxAudioPlays": "max_audio_plays",
        "position": "position",
        "partCode": "part_code",
    }
    updates: list[dict] = []
    for item in request.updates:
        raw = item.model_dump(exclude_unset=True)
        sid = raw.pop("id")
        patch: dict = {"id": sid}
        for k, v in raw.items():
            if k == "materials" and v is not None:
                patch["materials"] = v
            else:
                patch[field_map[k]] = v
        updates.append(patch)

    try:
        rows = await section_service.bulk_update_sections(updates)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return SectionBatchUpdateResponse(
        data=SectionListResponseData(items=[_to_view(s) for s in rows]),
    )


@section_router.post(
    "/batch-delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def batch_delete_sections(
    request: BatchDeleteRequest,
    hard: bool = False,
):
    """Delete up to 100 sections in one transaction (admin only).

    Soft delete by default. Pass `?hard=true` for hard delete (CASCADEs
    through questions/answers/state). Any missing id rolls back the whole
    batch.
    """
    try:
        await section_service.bulk_delete_sections(request.ids, hard=hard)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return None


@section_router.get("/{section_id}", response_model=SectionResponse)
async def get_section(
    section_id: str,
    include: Optional[str] = Query(default=None, description="comma-separated includes"),
    current_user: dict = Depends(get_current_user),
):
    """Get a section. Non-privileged users only see sections of published exams."""
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

    view = _to_view(section, strip_meta=not _is_privileged(current_user.get("role")))

    includes = {p.strip() for p in (include or "").split(",") if p.strip()}
    if "questions" in includes:
        # Imported lazily to avoid an import cycle (questions → sections membership check)
        from services.question_service import question_service
        from utils.grading_utils import strip_correct

        rows = await question_service.list_questions_by_section(section_id)
        is_priv = _is_privileged(current_user.get("role"))
        view.questions = [
            SectionQuestionPreview(
                id=q["id"],
                position=q["position"],
                questionType=q["question_type"],
                questionData=q["question_data"]
                if is_priv
                else strip_correct(q["question_type"], q["question_data"]),
                points=q["points"],
            )
            for q in rows
        ]

    return SectionResponse(data=SectionResponseData(section=view))


@section_router.put(
    "/{section_id}",
    response_model=SectionResponse,
    dependencies=[Depends(require_admin)],
)
async def update_section(section_id: str, request: SectionUpdate):
    """Update a section (admin only). Pass only the fields you want to change."""
    raw = request.model_dump(exclude_unset=True)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )

    # Translate camelCase API fields to snake_case service kwargs
    field_map = {
        "partLabel": "part_label",
        "type": "type",
        "instructions": "instructions",
        "materials": "materials",
        "maxAudioPlays": "max_audio_plays",
        "position": "position",
        "partCode": "part_code",
    }
    updates: dict = {}
    for k, v in raw.items():
        if k == "materials" and v is not None:
            # Pydantic gave us list[SectionMaterial] dicts already
            updates["materials"] = v
        else:
            updates[field_map[k]] = v

    try:
        section = await section_service.update_section(section_id, **updates)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SectionResponse(data=SectionResponseData(section=_to_view(section)))


@section_router.delete(
    "/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def soft_delete_section(section_id: str):
    """Soft-delete a section (admin only). Sets deleted_at; questions preserved."""
    try:
        await section_service.soft_delete_section(section_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return None


@section_router.delete(
    "/{section_id}/hard",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def hard_delete_section(section_id: str):
    """Hard-delete a section (admin only). CASCADEs through questions/answers/state."""
    try:
        await section_service.hard_delete_section(section_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return None


__all__ = ["exam_scoped_router", "section_router"]
