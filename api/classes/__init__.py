"""Class-management API package — admin + teacher + student routers."""

from .routes import admin_router, me_router, teacher_router

__all__ = ["admin_router", "me_router", "teacher_router"]
