"""Prompts + verify tool schema for AI image generation.

Provider-neutral building blocks consumed by the image adapter. See
docs/exam-image-generation §3, §5.
"""

from typing import Any, Optional


def _style_hint(exam_context: Optional[dict]) -> str:
    if not exam_context:
        return ""
    level = exam_context.get("level")
    skill = exam_context.get("skill")
    bits = [b for b in (f"level {level}" if level else "", f"{skill}" if skill else "") if b]
    return (f" Style: a clear, realistic {' '.join(bits)} English-exam image."
            if bits else "")


def build_generate_prompt(description: str, exam_context: Optional[dict] = None) -> str:
    return (
        "Generate an image for an English exam material. The image must depict "
        "EXACTLY the following, and any text/numbers it contains must be correct "
        "and legible (the exam questions depend on them):\n\n"
        f"{description}{_style_hint(exam_context)}"
    )


def build_edit_instruction(description: str, exam_context: Optional[dict] = None) -> str:
    return (
        "Edit the given image to match this new description, KEEPING the same "
        "layout/structure where possible (especially forms, signs, notices). Any "
        "text/numbers must be correct and legible:\n\n"
        f"{description}{_style_hint(exam_context)}"
    )


def build_verify_message(description: str) -> str:
    return (
        "You are checking an exam image. Does the image below depict EXACTLY this "
        "description, with any required text/numbers present, correct and legible?\n\n"
        f"DESCRIPTION:\n{description}\n\n"
        "Report via the `report_image_review` tool. Mark is_acceptable=false (with a "
        "concrete reason) if anything is wrong, missing, illegible, or mismatched."
    )


VERIFY_IMAGE_TOOL: dict[str, Any] = {
    "name": "report_image_review",
    "description": "Report whether the generated image matches the description.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_acceptable": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["is_acceptable", "reason"],
    },
}
