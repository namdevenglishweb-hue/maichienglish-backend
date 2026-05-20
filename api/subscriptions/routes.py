from fastapi import APIRouter, Depends, HTTPException, status

from dependencies import get_current_user
from services.subscription_service import list_plans, subscription_service
from services.user_service import user_service

from .schemas import (
    PlansResponse,
    PlansResponseData,
    PlanView,
    SubscriptionMeResponse,
    SubscriptionMeResponseData,
    SubscriptionView,
)

router = APIRouter(prefix="/api/subscriptions", tags=["Subscriptions"])


@router.get("/me", response_model=SubscriptionMeResponse)
async def get_my_subscription(current_user: dict = Depends(get_current_user)):
    """Return the current user's subscription row.

    - **Authorization**: Bearer access token required.
    """
    user = await user_service.get_by_email(current_user["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    sub = await subscription_service.get_by_user_id(user["id"])
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    return SubscriptionMeResponse(
        data=SubscriptionMeResponseData(
            subscription=SubscriptionView(
                tier=sub["tier"],
                status=sub["status"],
                creditsMonthly=sub["credits_monthly"],
                creditsRemaining=sub["credits_remaining"],
                currentPeriodStart=sub["current_period_start"],
                currentPeriodEnd=sub["current_period_end"],
            ),
        ),
    )


@router.get("/plans", response_model=PlansResponse)
async def get_plans():
    """Return the static catalog of subscription plans (Free / Basic / Pro / Ultra).

    Public endpoint — no authentication required.
    """
    plans = [PlanView(**p) for p in list_plans()]
    return PlansResponse(data=PlansResponseData(plans=plans))
