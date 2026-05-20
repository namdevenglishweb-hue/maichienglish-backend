from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=1, description="User password")


class RefreshRequest(BaseModel):
    refreshToken: str = Field(..., description="Refresh token issued at login")


class TokenData(BaseModel):
    accessToken: str
    refreshToken: str
    expiresIn: int = Field(description="Access token expiry in seconds")


class UserSubscriptionView(BaseModel):
    tier: str
    creditsRemaining: int = 0


class UserLoginView(BaseModel):
    id: str
    email: str
    fullName: str
    role: str
    subscription: UserSubscriptionView


class LoginResponseData(BaseModel):
    user: UserLoginView
    token: TokenData


class LoginResponse(BaseModel):
    status: int = 200
    data: LoginResponseData


class RefreshResponseData(BaseModel):
    token: TokenData


class RefreshResponse(BaseModel):
    status: int = 200
    data: RefreshResponseData


class VerifyUser(BaseModel):
    id: Optional[str] = None
    email: str
    role: str
    tier: str


class VerifyResponse(BaseModel):
    valid: bool = True
    user: VerifyUser
