from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class AdminCreateUserRequest(BaseModel):
    """Body for POST /api/admin/users (admin only)."""

    email: EmailStr = Field(..., description="User email; must be a real-world TLD")
    password: str = Field(..., min_length=6, description="Initial password (>=6 chars)")
    fullName: str = Field(..., min_length=1, description="Full name shown in UI")
    role: Literal["student", "teacher", "admin", "parent"] = Field(
        default="student", description="Role granted to the new user"
    )
    phone: Optional[str] = Field(default=None, description="Optional phone number")
    subscriptionTier: Literal["free", "basic", "pro", "ultra"] = Field(
        default="free", description="Initial subscription tier"
    )
    parentId: Optional[str] = Field(
        default=None,
        description="UUID of an existing parent profile. Only honored when role='student'.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "student@maichienglish.com",
                "password": "tempPassword123",
                "fullName": "Nguyen Van B",
                "role": "student",
                "phone": "0909876543",
                "subscriptionTier": "basic",
                "parentId": None,
            }
        }
    }


class AdminUserView(BaseModel):
    """User payload returned by admin endpoints."""

    id: str
    email: str
    fullName: str
    role: str
    phone: Optional[str] = None
    tier: str
    parentId: Optional[str] = None
    createdAt: Optional[str] = None


class AdminCreateUserResponseData(BaseModel):
    user: AdminUserView


class AdminCreateUserResponse(BaseModel):
    """Wrapped POST /api/admin/users response."""

    status: int = 201
    data: AdminCreateUserResponseData


class PaginationView(BaseModel):
    """Pagination metadata per §10.10."""

    page: int = Field(..., ge=1)
    limit: int = Field(..., ge=1)
    total: int = Field(..., ge=0)
    totalPages: int = Field(..., ge=0)


class AdminUserListResponseData(BaseModel):
    users: list[AdminUserView]
    pagination: PaginationView


class AdminUserListResponse(BaseModel):
    """Wrapped GET /api/admin/users response."""

    status: int = 200
    data: AdminUserListResponseData


class AdminResetPasswordRequest(BaseModel):
    """Body for POST /api/admin/users/{user_id}/reset-password."""

    newPassword: str = Field(..., min_length=6, description="New password (>=6 chars)")

    model_config = {
        "json_schema_extra": {"example": {"newPassword": "newTempPassword123"}}
    }


class AdminLinkParentRequest(BaseModel):
    """Body for PUT /api/admin/users/{student_id}/parent."""

    parentId: Optional[str] = Field(
        ...,
        description="UUID of an existing parent profile, or null to unlink.",
    )

    model_config = {
        "json_schema_extra": {"example": {"parentId": "uuid-of-existing-parent"}}
    }


class AdminLinkParentView(BaseModel):
    """Minimal view returned by the link-parent endpoint."""

    id: str
    role: str
    parentId: Optional[str] = None


class AdminLinkParentResponseData(BaseModel):
    user: AdminLinkParentView


class AdminLinkParentResponse(BaseModel):
    """Wrapped PUT /api/admin/users/{student_id}/parent response."""

    status: int = 200
    data: AdminLinkParentResponseData


class AdminUpdateSubscriptionRequest(BaseModel):
    """Body for PUT /api/admin/subscriptions/{user_id}."""

    tier: Literal["free", "basic", "pro", "ultra"] = Field(
        ..., description="Target subscription tier"
    )

    model_config = {"json_schema_extra": {"example": {"tier": "pro"}}}


class AdminSubscriptionView(BaseModel):
    """Subscription row returned by admin update endpoint."""

    userId: str
    tier: str
    status: str
    creditsMonthly: int
    creditsRemaining: int


class AdminUpdateSubscriptionResponseData(BaseModel):
    subscription: AdminSubscriptionView


class AdminUpdateSubscriptionResponse(BaseModel):
    """Wrapped PUT /api/admin/subscriptions/{user_id} response."""

    status: int = 200
    data: AdminUpdateSubscriptionResponseData
