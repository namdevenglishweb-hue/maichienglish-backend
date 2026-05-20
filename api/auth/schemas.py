from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Body for POST /api/auth/login."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=1, description="User password (plaintext over TLS)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "user@example.com",
                "password": "password123",
            }
        }
    }


class RefreshRequest(BaseModel):
    """Body for POST /api/auth/refresh."""

    refreshToken: str = Field(..., description="Refresh token issued at login")

    model_config = {
        "json_schema_extra": {
            "example": {"refreshToken": "eyJhbGciOi..."},
        }
    }


class TokenData(BaseModel):
    """JWT access + refresh tokens."""

    accessToken: str
    refreshToken: str
    expiresIn: int = Field(..., description="Access token expiry in seconds")


class UserSubscriptionView(BaseModel):
    """Minimal subscription view embedded in login responses."""

    tier: str = Field(..., description="free / basic / pro / ultra")
    creditsRemaining: int = 0


class UserLoginView(BaseModel):
    """User profile fields returned on login."""

    id: str
    email: str
    fullName: str
    role: str = Field(..., description="student / teacher / admin")
    subscription: UserSubscriptionView


class LoginResponseData(BaseModel):
    user: UserLoginView
    token: TokenData


class LoginResponse(BaseModel):
    """Wrapped POST /api/auth/login response."""

    status: int = 200
    data: LoginResponseData


class RefreshResponseData(BaseModel):
    token: TokenData


class RefreshResponse(BaseModel):
    """Wrapped POST /api/auth/refresh response."""

    status: int = 200
    data: RefreshResponseData


class VerifyUser(BaseModel):
    """Decoded user claims returned by /verify."""

    id: Optional[str] = None
    email: str
    role: str
    tier: str


class VerifyResponseData(BaseModel):
    valid: bool = True
    user: VerifyUser


class VerifyResponse(BaseModel):
    """Wrapped POST /api/auth/verify response."""

    status: int = 200
    data: VerifyResponseData
