"""AI exam generation HTTP routes (admin-only, §14).

All generation runs in the background (FastAPI BackgroundTasks); the FE polls
the job. Save (assembled-exam) is synchronous (no AI). Source validation +
media-meta precondition (§5.3) run BEFORE the job is created so the caller
gets a sync 400/404 instead of a failed job.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from dependencies import get_current_user, require_admin
from services.ai.generator import KNOWN_PROVIDERS
from services.exceptions import NotFoundError, ValidationError
from services.exam_generation_service import exam_generation_service
from services.generation_job_service import (
    generation_job_service,
    run_generation_job,
)
from services.user_service import user_service

from .schemas import (
    AssembledExamData,
    AssembledExamResponse,
    AssembleRequest,
    GenerateExamRequest,
    GenerateSectionRequest,
    JobAcceptedResponse,
    JobListResponse,
    JobListResponseData,
    JobView,
    ModelCatalogResponse,
    ModelListResponse,
    PreviewRequest,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/exam-generations",
    tags=["Admin · AI Exam Generation"],
    dependencies=[Depends(require_admin)],
)


async def _admin_id(current_user: dict) -> str | None:
    profile = await user_service.get_by_email(current_user["sub"])
    return profile["id"] if profile else None


def _check_provider(provider: str | None) -> None:
    """400 on an unknown aiProvider override (an invalid model just fails the job)."""
    if provider and provider not in KNOWN_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown aiProvider {provider!r}; allowed: {', '.join(KNOWN_PROVIDERS)}",
        )


def _check_prompt_version(version: str | None) -> None:
    """400 on an unknown promptVersion override (registry-driven, like provider)."""
    if version is None:
        return
    from services.ai import prompts as ai_prompts
    try:
        ai_prompts.get_prompt_version(version)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


# --------------------------------------------------------------------- #
# Create-job endpoints (202 + jobId, run in background)                  #
# --------------------------------------------------------------------- #


@admin_router.post(
    "", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def generate_exam(
    request: GenerateExamRequest,
    background: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Mode 1 — generate a whole exam, all-or-nothing, auto-saved as a draft."""
    _check_provider(request.aiProvider)
    _check_prompt_version(request.promptVersion)
    try:
        await exam_generation_service.precheck_exam_source(request.sourceExamId)
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    admin_id = await _admin_id(current_user)
    job = await generation_job_service.create_job(
        scope="exam", source_exam_id=request.sourceExamId, k=request.k,
        title=request.title, created_by=admin_id,
    )
    background.add_task(
        run_generation_job, job_id=job["jobId"], scope="exam",
        source_exam_id=request.sourceExamId, k=request.k, title=request.title,
        created_by=admin_id, section_prompts=request.sectionPrompts,
        model=request.aiModel, provider=request.aiProvider,
        prompt_version=request.promptVersion,
    )
    return JobAcceptedResponse(jobId=job["jobId"], status=job["status"])


@admin_router.post(
    "/section", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def generate_section(
    request: GenerateSectionRequest,
    background: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Mode 2 single part — returns the section payload in report.sections[0]."""
    _check_provider(request.aiProvider)
    _check_prompt_version(request.promptVersion)
    try:
        from services.presets import resolve_preset
        resolve_preset(request.partCode)  # 400 early on unknown part_code
        await exam_generation_service.precheck_section_source(request.sourceSectionId)
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    section, _ = await exam_generation_service.load_section_for_gen(request.sourceSectionId)
    job = await generation_job_service.create_job(
        scope="section", source_exam_id=section["exam_id"],
        target_section_id=request.sourceSectionId, k=request.k,
        created_by=await _admin_id(current_user),
    )
    background.add_task(
        run_generation_job, job_id=job["jobId"], scope="section",
        source_exam_id=job["sourceExamId"], target_section_id=request.sourceSectionId,
        k=request.k,
        section_prompts={request.sourceSectionId: request.sectionPrompt}
        if request.sectionPrompt else None,
        model=request.aiModel, provider=request.aiProvider,
        prompt_version=request.promptVersion, part_code=request.partCode,
    )
    return JobAcceptedResponse(jobId=job["jobId"], status=job["status"])


@admin_router.post(
    "/preview", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def generate_preview(
    request: PreviewRequest,
    background: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Mode 2 — generate all parts at once, per-part status, NOT saved."""
    _check_provider(request.aiProvider)
    _check_prompt_version(request.promptVersion)
    try:
        await exam_generation_service.precheck_exam_source(request.sourceExamId)
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    job = await generation_job_service.create_job(
        scope="exam_preview", source_exam_id=request.sourceExamId, k=request.k,
        created_by=await _admin_id(current_user),
    )
    background.add_task(
        run_generation_job, job_id=job["jobId"], scope="exam_preview",
        source_exam_id=request.sourceExamId, k=request.k,
        section_prompts=request.sectionPrompts,
        model=request.aiModel, provider=request.aiProvider,
        prompt_version=request.promptVersion,
    )
    return JobAcceptedResponse(jobId=job["jobId"], status=job["status"])


# --------------------------------------------------------------------- #
# Poll / manage                                                         #
# --------------------------------------------------------------------- #


@admin_router.get("/models", response_model=ModelListResponse)
async def list_models(provider: str | None = Query(default=None)):
    """Available model ids for an OpenAI-compatible provider (FE dropdown).

    Defaults to the env provider. Returns [] for providers without a listing
    API (e.g. anthropic). Declared BEFORE /{job_id} so it isn't shadowed.
    """
    from config.settings import get_settings

    s = get_settings()
    provider = provider or s.ai_provider
    _check_provider(provider)
    creds = {
        "openrouter": (s.openrouter_api_key, s.openrouter_base_url),
        "groq": (s.groq_api_key, s.groq_base_url),
        "gemini": (s.gemini_api_key, s.gemini_base_url),
    }.get(provider)
    if not creds:
        return ModelListResponse(provider=provider, models=[])
    api_key, base_url = creds
    if not api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"No API key for {provider}")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    try:
        listed = await client.models.list()
    except Exception as e:  # noqa: BLE001 — surface provider error as 502
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"{provider} models list failed: {e}")
    return ModelListResponse(provider=provider, models=sorted(m.id for m in listed.data))


@admin_router.get("/model-catalog", response_model=ModelCatalogResponse)
async def model_catalog():
    """Curated model list for the FE generation dropdown.

    Short hand-picked list (provider + model + label + note) of combos
    verified with the pipeline, plus the currently-effective default
    (ai-settings DB override or env). Edit the list in ONE place:
    services/ai/catalog.py. For the full raw provider listing use
    GET /models. Declared BEFORE /{job_id} so it isn't shadowed.
    """
    from services.ai.catalog import get_model_catalog

    return ModelCatalogResponse(**await get_model_catalog())


@admin_router.get("/{job_id}", response_model=JobView)
async def get_job(job_id: str):
    try:
        return JobView(**await generation_job_service.get_job(job_id))
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@admin_router.get("", response_model=JobListResponse)
async def list_jobs(
    status_: str | None = Query(default=None, alias="status"),
    scope: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    jobs = await generation_job_service.list_jobs(
        status=status_, scope=scope, limit=limit, offset=offset
    )
    return JobListResponse(
        data=JobListResponseData(items=[JobView(**j) for j in jobs])
    )


@admin_router.post("/{job_id}/cancel", response_model=JobView)
async def cancel_job(job_id: str):
    try:
        return JobView(**await generation_job_service.request_cancel(job_id))
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


# --------------------------------------------------------------------- #
# Save assembled draft (Mode 2, synchronous)                            #
# --------------------------------------------------------------------- #


@admin_router.post(
    "/assembled-exam", response_model=AssembledExamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_assembled_exam(
    request: AssembleRequest,
    current_user: dict = Depends(get_current_user),
):
    _check_prompt_version(request.promptVersion)
    try:
        result = await exam_generation_service.assemble_generated_exam(
            request.sourceExamId, request.sections, title=request.title,
            created_by=await _admin_id(current_user), k=request.k,
            section_prompts=request.sectionPrompts,
            prompt_version=request.promptVersion,
        )
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    # Serialize via the shared exam view → camelCase, like every other exam
    # response. The creating admin sees generationMeta (include_meta=True).
    from api.exams.routes import _to_view
    return AssembledExamResponse(
        data=AssembledExamData(exam=_to_view(result["exam"]), warning=result["warning"])
    )
