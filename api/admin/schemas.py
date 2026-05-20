from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    fullName: str = Field(..., min_length=1)
    role: Literal["student", "teacher", "admin"] = "student"
    phone: Optional[str] = None
    subscriptionTier: Literal["free", "basic", "pro", "ultra"] = "free"


class AdminUserView(BaseModel):
    id: str
    email: str
    fullName: str
    role: str
    phone: Optional[str] = None
    tier: str
    createdAt: Optional[str] = None


class AdminCreateUserResponseData(BaseModel):
    user: AdminUserView


class AdminCreateUserResponse(BaseModel):
    status: int = 201
    data: AdminCreateUserResponseData


class AdminResetPasswordRequest(BaseModel):
    newPassword: str = Field(..., min_length=6)


class AdminUpdateSubscriptionRequest(BaseModel):
    tier: Literal["free", "basic", "pro", "ultra"]


class AdminSubscriptionView(BaseModel):
    userId: str
    tier: str
    status: str
    creditsMonthly: int
    creditsRemaining: int


class AdminUpdateSubscriptionResponseData(BaseModel):
    subscription: AdminSubscriptionView


class AdminUpdateSubscriptionResponse(BaseModel):
    status: int = 200
    data: AdminUpdateSubscriptionResponseData
