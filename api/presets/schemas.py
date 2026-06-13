"""Response schema for GET /api/presets (typed OpenAPI contract)."""

from pydantic import BaseModel, Field


class PresetItem(BaseModel):
    partCode: str = Field(..., description="Preset id, e.g. 'KET_R_P3'.")
    level: str = Field(..., description="KET | PET.")
    skill: str = Field(..., description="reading | listening | writing | speaking.")
    label: str = Field(..., description="Cambridge part label, e.g. 'Part 3'.")
    labelVi: str = Field(..., description="Short Vietnamese description for the dropdown.")
    sectionType: str = Field(..., description="Maps to sections.type.")
    questionType: str = Field(..., description="Maps to questions.question_type.")
    numQuestions: int
    optionsPerQuestion: int
    wordCountRange: list[int] = Field(..., description="[min, max] words for the material.")
    cefrLevel: str
    pointsPerQuestion: int


class PresetListResponse(BaseModel):
    presets: list[PresetItem]
