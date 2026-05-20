from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user
from services.user_service import user_service

from .schemas import UserMeResponse, UserSubscriptionFull

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("/me", response_model=UserMeResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Return the current user's profile + subscription."""
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return UserMeResponse(
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
