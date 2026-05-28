from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


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


class AdminUserSubscriptionView(BaseModel):
    """Subscription summary nested inside admin-facing user payloads."""

    tier: str = Field(..., description="free / basic / pro / ultra")
    status: Optional[str] = Field(default=None, description="active / canceled / expired")
    creditsMonthly: int = Field(default=0, description="Monthly credit allowance")
    creditsRemaining: int = Field(default=0, description="Credits left in the current period")


class AdminUserView(BaseModel):
    """User payload returned by admin endpoints."""

    id: str
    email: str
    fullName: str
    role: str
    phone: Optional[str] = None
    subscription: AdminUserSubscriptionView
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

    page: int = Field(..., ge=1, description="1-based current page number")
    limit: int = Field(..., ge=1, description="Page size used to produce this slice")
    total: int = Field(..., ge=0, description="Total matching rows across all pages")
    totalPages: int = Field(..., ge=0, description="Total pages = ceil(total / limit)")


class AdminUserListResponseData(BaseModel):
    """Paginated list payload — `items` per §10.10."""

    items: list[AdminUserView]
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


class UploadRequest(BaseModel):
    """Body for POST /api/admin/upload — see MEDIA_UPLOAD.md §5.1."""

    bucket: Literal["audio", "images"] = Field(
        ..., description="Target storage bucket"
    )
    filename: str = Field(
        ...,
        min_length=1,
        description="Original filename (used only for error messages — extension is derived from contentType)",
    )
    contentType: str = Field(
        ..., min_length=1, description="MIME type — validated against the bucket's whitelist"
    )
    fileSizeBytes: int = Field(
        ..., ge=1, description="File size in bytes — validated against the bucket's size limit"
    )

    @model_validator(mode="after")
    def _cross_validate(self) -> "UploadRequest":
        from services.storage_service import ALLOWED_TYPES, EXT_FOR_MIME, SIZE_LIMITS

        allowed = ALLOWED_TYPES[self.bucket]
        if self.contentType not in allowed:
            raise ValueError(
                f'Invalid contentType "{self.contentType}" for bucket "{self.bucket}"; '
                f"allowed: {sorted(allowed)}"
            )
        if self.contentType not in EXT_FOR_MIME:
            raise ValueError(
                f'No extension mapping for contentType "{self.contentType}"'
            )
        limit = SIZE_LIMITS[self.bucket]
        if self.fileSizeBytes > limit:
            raise ValueError(
                f"File size {self.fileSizeBytes} exceeds limit of {limit} bytes "
                f'for bucket "{self.bucket}"'
            )
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "bucket": "audio",
                "filename": "ket-listening-p5.mp3",
                "contentType": "audio/mpeg",
                "fileSizeBytes": 3500000,
            }
        }
    }


class UploadResponseData(BaseModel):
    uploadUrl: str = Field(..., description="Signed PUT URL (Supabase fixes TTL at 2h)")
    publicUrl: str = Field(..., description="Permanent URL to persist in DB")
    token: str = Field(
        ...,
        description="Bearer token from uploadUrl query; usable with @supabase/supabase-js .uploadToSignedUrl()",
    )
    path: str = Field(..., description="Storage path within the bucket")
    bucket: Literal["audio", "images"]


class UploadResponse(BaseModel):
    """Wrapped POST /api/admin/upload response."""

    status: int = 200
    data: UploadResponseData
