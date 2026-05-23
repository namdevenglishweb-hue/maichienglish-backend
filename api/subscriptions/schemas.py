from typing import Optional

from pydantic import BaseModel, Field


class SubscriptionView(BaseModel):
    """Full subscription row as returned to the user."""

    tier: str = Field(..., description="free / basic / pro / ultra")
    status: str = Field(..., description="active / canceled / expired")
    creditsMonthly: int
    creditsRemaining: int
    currentPeriodStart: Optional[str] = Field(default=None, description="ISO-8601 timestamp")
    currentPeriodEnd: Optional[str] = Field(default=None, description="ISO-8601 timestamp")


class SubscriptionMeResponseData(BaseModel):
    subscription: SubscriptionView


class SubscriptionMeResponse(BaseModel):
    """Wrapped GET /api/subscriptions/me response."""

    status: int = 200
    data: SubscriptionMeResponseData


class PlanFeatureView(BaseModel):
    key: str
    name: str
    active: bool
    value: Optional[str] = None


class PlanView(BaseModel):
    tier: str
    name: str
    priceMonthly: float
    attemptsMonthly: int = Field(..., description="-1 = unlimited")
    features: list[PlanFeatureView]


class PlansResponseData(BaseModel):
    """List payload — `items` per §10.10 list convention."""

    items: list[PlanView]


class PlansResponse(BaseModel):
    """Wrapped GET /api/subscriptions/plans response."""

    status: int = 200
    data: PlansResponseData
