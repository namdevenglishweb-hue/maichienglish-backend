from typing import Optional

from pydantic import BaseModel


class SubscriptionView(BaseModel):
    tier: str
    status: str
    creditsMonthly: int
    creditsRemaining: int
    currentPeriodStart: Optional[str] = None
    currentPeriodEnd: Optional[str] = None


class PlanFeatureView(BaseModel):
    key: str
    name: str
    active: bool
    value: Optional[str] = None


class PlanView(BaseModel):
    tier: str
    name: str
    priceMonthly: float
    attemptsMonthly: int
    features: list[PlanFeatureView]


class PlansResponse(BaseModel):
    plans: list[PlanView]
