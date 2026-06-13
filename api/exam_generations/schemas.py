"""Request/response schemas for AI exam generation (admin, §14)."""

from typing import Any, Optional

from pydantic import BaseModel, Field

from api.exams.schemas import ExamView


class _ModelOverride(BaseModel):
    """Optional per-request AI model/provider override (else env defaults).

    Lets the FE/admin try different models without a redeploy. `aiProvider` ∈
    {openrouter, groq, gemini, anthropic}; `aiModel` is the provider-specific
    id (e.g. llama-3.3-70b-versatile, anthropic/claude-sonnet-4.5).
    """

    aiModel: Optional[str] = Field(default=None, description="Override AI_MODEL for this run.")
    aiProvider: Optional[str] = Field(default=None, description="Override AI_PROVIDER for this run.")
    promptVersion: Optional[str] = Field(
        default=None,
        description="Prompt-pipeline version for this run: 'v1' (legacy), "
        "'v2' (anti-clone, the DEFAULT), 'v3' (spec mode, K>=3 never sees "
        "the source). Unknown version → 400. Combine with aiModel/aiProvider "
        "to A/B {version × model}.",
    )


class GenerateExamRequest(_ModelOverride):
    """Mode 1 — whole exam, auto-save (POST /api/admin/exam-generations)."""

    sourceExamId: str
    k: int = Field(..., ge=1, le=5, description="Variation level 1..5 (§3).")
    title: Optional[str] = Field(
        default=None, description="New exam title. Default '{source} (AI K{k})'."
    )
    sectionPrompts: Optional[dict[str, str]] = Field(
        default=None,
        description="Per-section ad-hoc prompt (B, §10.2), keyed by source sectionId.",
    )


class GenerateSectionRequest(_ModelOverride):
    """Mode 2 single part (POST .../section)."""

    sourceSectionId: str
    k: int = Field(..., ge=1, le=5)
    sectionPrompt: Optional[str] = None
    partCode: Optional[str] = Field(
        default=None,
        description="Bind this part to a Cambridge preset (e.g. 'KET_R_P3', "
        "'PET_R_P3'). When set, the generated structure follows the PRESET "
        "(num questions / options / word-count / CEFR), not the source. "
        "Unknown code → 400. Read GET /api/presets for the list.",
    )


class PreviewRequest(_ModelOverride):
    """Mode 2 all-parts preview, no save (POST .../preview)."""

    sourceExamId: str
    k: int = Field(..., ge=1, le=5)
    sectionPrompts: Optional[dict[str, str]] = None


class AssembleRequest(BaseModel):
    """Mode 2 Save (POST .../assembled-exam). `sections` are generated section
    payloads held client-side; re-validated server-side by create_exam_nested."""

    sourceExamId: str
    title: Optional[str] = None
    k: Optional[int] = Field(default=None, ge=1, le=5)
    sections: list[dict[str, Any]] = Field(..., min_length=1)
    sectionPrompts: Optional[dict[str, str]] = None
    promptVersion: Optional[str] = Field(
        default=None,
        description="Prompt version the parts were generated with (provenance "
        "for generation_meta). Unknown version → 400.",
    )


class ModelListResponse(BaseModel):
    """GET /models — raw passthrough of every model id the provider offers.

    Power-user listing (long, unlabeled). For the FE dropdown prefer
    GET /model-catalog (curated + labels + default)."""

    provider: str = Field(..., description="Provider that was queried (defaults to env).")
    models: list[str] = Field(
        default_factory=list,
        description="Provider-reported model ids, sorted. Empty for providers "
        "without a listing API (e.g. anthropic direct).",
    )


class ModelInfo(BaseModel):
    """One curated model entry (services/ai/catalog.py — the single place to edit)."""

    provider: str = Field(..., description="One of generator.KNOWN_PROVIDERS.")
    model: str = Field(..., description="Provider-specific model id/slug.")
    label: str = Field(..., description="Human-readable name for the dropdown.")
    note: Optional[str] = Field(default=None, description="Caveat/hint shown in the picker.")


class ModelDefault(BaseModel):
    """The currently-effective default (per-request > ai-settings DB > env)."""

    provider: str = Field(..., description="Effective default provider id.")
    model: str = Field(..., description="Effective default model id for that provider.")


class ModelCatalogResponse(BaseModel):
    """GET /model-catalog — curated picker list + effective default."""

    default: ModelDefault
    models: list[ModelInfo]


class JobAcceptedResponse(BaseModel):
    """202 body for create-job endpoints — poll GET /{jobId} for progress."""

    jobId: str = Field(..., description="Id of the created background job.")
    status: str = Field(default="pending", description="Initial job status (always 'pending').")


class JobView(BaseModel):
    """Polled job state (§14.2). `report` shape varies by scope."""

    jobId: str = Field(..., description="Job id.")
    scope: str = Field(..., description="Job scope: 'exam' | 'section' | 'exam_preview'.")
    status: str = Field(..., description="pending | running | succeeded | failed | aborted.")
    sourceExamId: str = Field(..., description="Source exam the job was generated from.")
    targetSectionId: Optional[str] = Field(
        default=None, description="Source section id (section scope only)."
    )
    k: int = Field(..., description="Variation level 1..5 the job ran with.")
    title: Optional[str] = Field(default=None, description="Title of the exam being generated.")
    sectionsTotal: Optional[int] = Field(
        default=None, description="Total sections to generate (multi-part scopes)."
    )
    sectionsDone: int = Field(default=0, description="Sections completed so far.")
    currentSection: Optional[int] = Field(
        default=None, description="1-based index of the section in progress."
    )
    resultExamId: Optional[str] = Field(
        default=None, description="Saved exam id once a Mode-1 job finishes."
    )
    report: Optional[dict[str, Any]] = Field(
        default=None, description="Scope-dependent result/diagnostics payload."
    )
    abortedReason: Optional[str] = Field(
        default=None, description="Why the job was cancelled/failed, if applicable."
    )
    createdAt: Optional[str] = Field(default=None, description="ISO-8601 creation time.")
    updatedAt: Optional[str] = Field(default=None, description="ISO-8601 last-update time.")
    finishedAt: Optional[str] = Field(default=None, description="ISO-8601 completion time.")


class JobListResponseData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[JobView]


class JobListResponse(BaseModel):
    """Wrapped response for GET /api/admin/exam-generations."""

    status: int = 200
    data: JobListResponseData


class AssembledExamData(BaseModel):
    """Save-result payload — the created draft exam plus any non-fatal warning."""

    exam: ExamView = Field(
        ..., description="Created draft exam (camelCase, like every other exam response)."
    )
    warning: Optional[str] = Field(
        default=None, description="Non-fatal note surfaced during assembly, if any."
    )


class AssembledExamResponse(BaseModel):
    status: int = 201
    data: AssembledExamData
