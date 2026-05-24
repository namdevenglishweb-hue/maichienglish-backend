"""Section endpoints — middle layer of Exam → Section → Question."""
from .routes import exam_scoped_router, section_router

__all__ = ["exam_scoped_router", "section_router"]
