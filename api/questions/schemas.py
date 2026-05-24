from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

QuestionTypeLiteral = Literal["multiple_choice", "fill_blank", "matching"]


class QuestionCreate(BaseModel):
    """Body for POST /api/sections/{section_id}/questions (admin only).

    `question_data` shape is validated server-side per `question_type`:
      - multiple_choice: {
            "stem"?: str,
            "options": [{text?, image_url?}, ...],   # >=2; each option has text and/or image_url
            "correct_index": int
        }
      - fill_blank:      {"correct_answers": [...], "case_sensitive": bool}
      - matching:        {"left": [...], "right": [...], "correct_pairs": [[int,int], ...]}
    """

    question_type: QuestionTypeLiteral = Field(..., description="One of the 3 supported types")
    question_data: dict[str, Any] = Field(
        ..., description="Type-specific payload, validated server-side"
    )
    points: int = Field(default=1, ge=0, description="Points awarded when answered correctly")
    position: Optional[int] = Field(
        default=None,
        description="Order within the section (used by {{gap:N}} markers). If omitted, server appends to the end.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "question_type": "multiple_choice",
                    "question_data": {
                        "stem": "Chloe wants Susie...",
                        "options": [
                            {"text": "to clean her room."},
                            {"text": "to stop working at home."},
                            {"text": "to tidy up the living room."},
                        ],
                        "correct_index": 2,
                    },
                    "points": 1,
                },
                {
                    "question_type": "multiple_choice",
                    "question_data": {
                        "stem": "How did the woman travel to work this morning?",
                        "options": [
                            {"image_url": "https://.../car.png"},
                            {"image_url": "https://.../train.png"},
                            {"image_url": "https://.../bus.png"},
                        ],
                        "correct_index": 2,
                    },
                    "points": 1,
                },
                {
                    "question_type": "fill_blank",
                    "question_data": {
                        "correct_answers": ["nine", "9"],
                        "case_sensitive": False,
                    },
                    "points": 1,
                },
                {
                    "question_type": "matching",
                    "question_data": {
                        "left": ["dining room", "bathroom", "bedroom"],
                        "right": ["bookcase", "clock", "cupboard"],
                        "correct_pairs": [[0, 0], [1, 1], [2, 2]],
                    },
                    "points": 3,
                },
            ]
        }
    }


class QuestionUpdate(BaseModel):
    """Body for PUT /api/questions/{question_id}. Omit a field to leave it unchanged.

    Changing `question_type` requires also supplying a matching `question_data`.
    """

    question_type: Optional[QuestionTypeLiteral] = None
    question_data: Optional[dict[str, Any]] = None
    points: Optional[int] = Field(default=None, ge=0)
    position: Optional[int] = None


class QuestionView(BaseModel):
    """Question payload returned to clients."""

    id: str
    sectionId: str
    position: int
    questionType: str
    questionData: dict[str, Any]
    points: int
    createdAt: Optional[str] = None
    deletedAt: Optional[str] = None


class QuestionResponseData(BaseModel):
    question: QuestionView


class QuestionResponse(BaseModel):
    """Wrapped response for single-question endpoints."""

    status: int = 200
    data: QuestionResponseData


class QuestionListResponseData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[QuestionView]


class QuestionListResponse(BaseModel):
    """Wrapped response for GET /api/sections/{section_id}/questions."""

    status: int = 200
    data: QuestionListResponseData
