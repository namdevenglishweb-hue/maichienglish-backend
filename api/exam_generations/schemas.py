"""Request/response schemas for AI exam generation (admin, §14)."""

from typing import Any, Optional

from pydantic import BaseModel, Field

from api.exams.schemas import ExamView


class _ModelOverride(BaseModel):
    """Optional per-request AI model/provider override (else env defaults).

    Lets the FE/admin try different models without a redeploy. `aiProvider` ∈
    {openrouter, groq, anthropic}; `aiModel` is the provider-specific id
    (e.g. llama-3.3-70b-versatile, google/gemini-2.5-pro, claude-sonnet-4-6).
    """

    aiModel: Optional[str] = Field(default=None, description="Override AI_MODEL for this run.")
    aiProvider: Optional[str] = Field(default=None, description="Override AI_PROVIDER for this run.")
    promptVersion: Optional[str] = Field(
        default=None,
        description="Prompt-pipeline version for this run (e.g. 'v1', 'v2'). "
        "Default v1 (production baseline). Unknown version → 400. "
        "Combine with aiModel/aiProvider to A/B {version × model}.",
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

    provider: str
    model: str


class ModelCatalogResponse(BaseModel):
    """GET /model-catalog — curated picker list + effective default."""

    default: ModelDefault
    models: list[ModelInfo]


class JobAcceptedResponse(BaseModel):
    jobId: str
    status: str = "pending"


class JobView(BaseModel):
    """Polled job state (§14.2). `report` shape varies by scope."""

    jobId: str
    scope: str
    status: str
    sourceExamId: str
    targetSectionId: Optional[str] = None
    k: int
    title: Optional[str] = None
    sectionsTotal: Optional[int] = None
    sectionsDone: int = 0
    currentSection: Optional[int] = None
    resultExamId: Optional[str] = None
    report: Optional[dict[str, Any]] = None
    abortedReason: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    finishedAt: Optional[str] = None


class JobListResponse(BaseModel):
    status: int = 200
    data: dict[str, list[JobView]]


class AssembledExamData(BaseModel):
    exam: ExamView  # camelCase, consistent with the rest of the exam API
    warning: Optional[str] = None


class AssembledExamResponse(BaseModel):
    status: int = 201
    data: AssembledExamData
