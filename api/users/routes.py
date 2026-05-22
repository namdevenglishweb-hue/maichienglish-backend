from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user
from services.exceptions import NotFoundError, ValidationError
from services.user_service import user_service

from .schemas import (
    UserMeResponse,
    UserMeResponseData,
    UserMeView,
    UserProfileUpdate,
    UserSubscriptionFull,
)

router = APIRouter(prefix="/api/users", tags=["Users"])


def _user_to_view(user: dict) -> UserMeView:
    return UserMeView(
        id=user["id"],
        email=user["email"],
        fullName=user["full_name"],
        role=user["role"],
        phone=user["phone"],
        subscription=UserSubscriptionFull(
            tier=user["tier"],
            status=user["subscription_status"],
            creditsMonthly=user["credits_monthly"],
            creditsRemaining=user["credits_remaining"],
        ),
        createdAt=user["created_at"],
    )


@router.get("/me", response_model=UserMeResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Return the current user's profile + subscription.

    - **Authorization**: Bearer access token required.
    """
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return UserMeResponse(data=UserMeResponseData(user=_user_to_view(user)))


@router.put("/me", response_model=UserMeResponse)
async def update_me(
    request: UserProfileUpdate, current_user: dict = Depends(get_current_user)
):
    """Update the current user's mutable profile fields (fullName, phone).

    Email + role + subscription remain admin-managed.
    """
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )

    try:
        updated = await user_service.update_profile(
            user_id=user["id"],
            full_name=updates.get("fullName"),
            phone=updates.get("phone"),
        )
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return UserMeResponse(data=UserMeResponseData(user=_user_to_view(updated)))
