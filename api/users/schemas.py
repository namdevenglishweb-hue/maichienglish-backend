from typing import Optional

from pydantic import BaseModel


class UserSubscriptionFull(BaseModel):
    tier: str
    status: Optional[str] = None
    creditsMonthly: int = 0
    creditsRemaining: int = 0


class UserMeResponse(BaseModel):
    id: str
    email: str
    fullName: str
    role: str
    phone: Optional[str] = None
    subscription: UserSubscriptionFull
    createdAt: Optional[str] = None
