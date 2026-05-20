import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import require_admin
from services.exceptions import AlreadyExistsError, NotFoundError, ValidationError
from services.subscription_service import subscription_service
from services.user_service import user_service

from .schemas import (
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminCreateUserResponseData,
    AdminResetPasswordRequest,
    AdminSubscriptionView,
    AdminUpdateSubscriptionRequest,
    AdminUpdateSubscriptionResponse,
    AdminUpdateSubscriptionResponseData,
    AdminUserView,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
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
        )
    except AlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return AdminCreateUserResponse(
        status=201,
        data=AdminCreateUserResponseData(
            user=AdminUserView(
                id=user["id"],
                email=user["email"],
                fullName=user["full_name"],
                role=user["role"],
                phone=user["phone"],
                tier=user["tier"],
                createdAt=user["created_at"],
            )
        ),
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
