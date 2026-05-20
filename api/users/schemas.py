from typing import Optional

from pydantic import BaseModel, Field


class UserSubscriptionFull(BaseModel):
    """Subscription summary as nested in user-facing payloads."""

    tier: str = Field(..., description="Plan tier: free / basic / pro / ultra")
    status: Optional[str] = Field(default=None, description="active / canceled / expired")
    creditsMonthly: int = Field(default=0, description="Monthly credit allowance")
    creditsRemaining: int = Field(default=0, description="Credits left in the current period")


class UserMeView(BaseModel):
    """Current user profile + embedded subscription."""

    id: str = Field(..., description="User UUID")
    email: str
    fullName: str
    role: str = Field(..., description="student / teacher / admin")
    phone: Optional[str] = None
    subscription: UserSubscriptionFull
    createdAt: Optional[str] = Field(default=None, description="ISO-8601 timestamp")


class UserMeResponseData(BaseModel):
    user: UserMeView


class UserMeResponse(BaseModel):
    """Wrapped GET /api/users/me response (per §10.10)."""

    status: int = 200
    data: UserMeResponseData
