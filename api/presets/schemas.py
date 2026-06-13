"""Response schema for GET /api/presets (typed OpenAPI contract)."""

from typing import Optional

from pydantic import BaseModel, Field


class MaterialSpecItem(BaseModel):
    type: str = Field(..., description="text | audio | image.")
    count: int = Field(..., description="How many of this material the Part has.")
    note: str = Field(default="", description="Render hint / label convention.")


class PresetItem(BaseModel):
    partCode: str = Field(..., description="Preset id, e.g. 'KET_R_P3'.")
    level: str = Field(..., description="KET | PET.")
    skill: str = Field(..., description="reading | listening | writing | speaking.")
    defaultPosition: int = Field(..., description="Default order of this Part in a full exam.")
    label: str = Field(..., description="Cambridge part label, e.g. 'Part 3'.")
    labelVi: str = Field(..., description="Short Vietnamese description for the dropdown.")
    sectionType: str = Field(..., description="Maps to sections.type.")
    questionType: str = Field(..., description="Maps to questions.question_type.")
    numQuestions: int
    optionsPerQuestion: Optional[int] = Field(
        default=None, description="null for fill_blank/form_completion/writing/speaking.")
    wordCountRange: Optional[list[int]] = Field(
        default=None, description="[min, max] words for a single-text Part; null otherwise.")
    cefrLevel: str
    pointsPerQuestion: int
    gapMarkers: bool = Field(..., description="Material uses {{gap:N}} markers.")
    sharedOptions: bool = Field(..., description="All questions share one option table.")
    materialsSpec: list[MaterialSpecItem] = Field(
        default_factory=list, description="Required materials (count + type) for the Part.")
    instructionsEn: str = Field(default="", description="Default Cambridge-style rubric.")
    aiCore: Optional[str] = Field(
        default=None, description="Intended AI-gen core name; null = chưa định.")
    aiGenSupported: bool = Field(
        ..., description="True nếu Part này AI-gen được ngay (core đã implement). "
        "False = builder/scaffold được nhưng chưa AI-gen.")
    imageDependent: bool = Field(
        ..., description="True nếu Part cần hình ảnh (picture MC / story / mô tả ảnh "
        "/ visual prompt) — AI tạo ảnh để đợt sau.")


class PresetListResponse(BaseModel):
    presets: list[PresetItem]
