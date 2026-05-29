import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from dependencies import require_admin
from services.exceptions import AlreadyExistsError, NotFoundError, ValidationError
from services.storage_service import get_storage_service
from services.subscription_service import subscription_service
from services.user_service import user_service

from .schemas import (
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminCreateUserResponseData,
    AdminLinkParentRequest,
    AdminLinkParentResponse,
    AdminLinkParentResponseData,
    AdminLinkParentView,
    AdminResetPasswordRequest,
    AdminSubscriptionView,
    AdminUpdateSubscriptionRequest,
    AdminUpdateSubscriptionResponse,
    AdminUpdateSubscriptionResponseData,
    AdminUserListResponse,
    AdminUserListResponseData,
    AdminUserSubscriptionView,
    AdminUserView,
    PaginationView,
    UploadRequest,
    UploadResponse,
    UploadResponseData,
)


def _user_to_admin_view(u: dict) -> AdminUserView:
    return AdminUserView(
        id=u["id"],
        email=u["email"],
        fullName=u["full_name"],
        role=u["role"],
        phone=u["phone"],
        subscription=AdminUserSubscriptionView(
            tier=u["tier"],
            status=u.get("subscription_status"),
            creditsMonthly=u.get("credits_monthly", 0),
            creditsRemaining=u.get("credits_remaining", 0),
        ),
        parentId=u.get("parent_id"),
        createdAt=u.get("created_at"),
    )

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("/users", response_model=AdminUserListResponse)
async def admin_list_users(
    role: Optional[Literal["student", "teacher", "admin", "parent"]] = Query(
        default=None, description="Filter by role"
    ),
    page: int = Query(default=1, ge=1, description="1-based page number"),
    limit: int = Query(
        default=50, ge=1, le=100, description="Page size (max 100)"
    ),
):
    """List users (admin only). Most recent first.

    Supports optional `role` filter + page-based pagination.
    """
    offset = (page - 1) * limit
    users, total = await user_service.list_users(
        role=role, limit=limit, offset=offset
    )
    total_pages = (total + limit - 1) // limit if limit else 0

    return AdminUserListResponse(
        data=AdminUserListResponseData(
            items=[_user_to_admin_view(u) for u in users],
            pagination=PaginationView(
                page=page, limit=limit, total=total, totalPages=total_pages
            ),
        ),
    )


@router.post(
    "/users",
    response_model=AdminCreateUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_user(request: AdminCreateUserRequest):
    """Create a new user (admin only). Also creates the matching subscription row."""
    try:
        user = await user_service.create_user(
            email=str(request.email),
            password=request.password,
            full_name=request.fullName,
            role=request.role,
            phone=request.phone,
            tier=request.subscriptionTier,
            parent_id=request.parentId,
        )
    except AlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AdminCreateUserResponse(
        status=201,
        data=AdminCreateUserResponseData(user=_user_to_admin_view(user)),
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_user(user_id: str):
    """Hard-delete a user (cascades subscription / attempts / answers)."""
    try:
        await user_service.delete_user(user_id)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        )
    return None


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def admin_reset_password(user_id: str, request: AdminResetPasswordRequest):
    """Set a new password for a user (admin only)."""
    try:
        await user_service.admin_reset_password(user_id, request.newPassword)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        )
    return None


@router.put(
    "/users/{student_id}/parent",
    response_model=AdminLinkParentResponse,
)
async def admin_link_parent(student_id: str, request: AdminLinkParentRequest):
    """Link or unlink a parent on a student profile (admin only).

    - Target user must have `role='student'`.
    - `parentId` (if non-null) must reference a profile with `role='parent'`.
    - Pass `parentId: null` to unlink.
    """
    try:
        result = await user_service.link_parent(student_id, request.parentId)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AdminLinkParentResponse(
        data=AdminLinkParentResponseData(
            user=AdminLinkParentView(
                id=result["id"],
                role=result["role"],
                parentId=result["parent_id"],
            ),
        ),
    )


@router.put(
    "/subscriptions/{user_id}",
    response_model=AdminUpdateSubscriptionResponse,
)
async def admin_update_subscription(
    user_id: str, request: AdminUpdateSubscriptionRequest
):
    """Change a user's subscription tier (admin only)."""
    try:
        sub = await subscription_service.update_tier(user_id, request.tier)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return AdminUpdateSubscriptionResponse(
        data=AdminUpdateSubscriptionResponseData(
            subscription=AdminSubscriptionView(
                userId=sub["user_id"],
                tier=sub["tier"],
                status=sub["status"],
                creditsMonthly=sub["credits_monthly"],
                creditsRemaining=sub["credits_remaining"],
            )
        ),
    )


@router.post("/upload", response_model=UploadResponse, status_code=200)
async def admin_request_upload(request: UploadRequest):
    """Issue a signed URL for direct browser-to-storage upload.

    See MEDIA_UPLOAD.md. Validation happens in `UploadRequest`'s
    model_validator (returns 422). Storage transport failures map to
    503; missing env vars / adapter init failures map to 503 (so the
    response carries CORS headers — an unhandled RuntimeError out of
    the route bypasses CORSMiddleware and the browser surfaces it as
    a CORS error instead of the real 500).
    """
    try:
        storage = get_storage_service()
        result = await storage.create_signed_upload(
            bucket=request.bucket,
            content_type=request.contentType,
            file_size_bytes=request.fileSizeBytes,
        )
    except RuntimeError as e:
        logger.exception("storage adapter init failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage service not configured",
        )
    except Exception as e:
        status_code = getattr(e, "status_code", None) or getattr(e, "code", None)
        try:
            status_code = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            status_code = None
        if status_code is not None and 500 <= status_code < 600:
            logger.warning("storage unreachable: %s", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Storage service unavailable",
            )
        logger.exception("storage error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage error",
        )

    return UploadResponse(
        data=UploadResponseData(
            uploadUrl=result.upload_url,
            publicUrl=result.public_url,
            token=result.token,
            path=result.path,
            bucket=result.bucket,
        )
    )
