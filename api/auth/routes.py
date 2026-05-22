import logging

from fastapi import APIRouter, Depends, HTTPException, status

from config.settings import get_settings
from dependencies import get_current_user
from services.auth_service import auth_service
from services.exceptions import InvalidCredentialsError, ValidationError
from services.user_service import user_service
from utils.jwt_utils import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)

from .schemas import (
    LoginRequest,
    LoginResponse,
    LoginResponseData,
    PasswordResetCodeRequest,
    PasswordResetCodeResponse,
    PasswordResetCodeResponseData,
    PasswordResetRequest,
    PasswordResetResponse,
    PasswordResetResponseData,
    RefreshRequest,
    RefreshResponse,
    RefreshResponseData,
    TokenData,
    UserLoginView,
    UserSubscriptionView,
    VerifyResponse,
    VerifyResponseData,
    VerifyUser,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _expires_in_seconds() -> int:
    return get_settings().jwt_access_exp_minutes * 60


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Authenticate user and return JWT access + refresh tokens."""
    try:
        user = await user_service.authenticate(
            email=str(request.email),
            password=request.password,
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không đúng",
        )

    access_token = create_access_token(
        email=user["email"],
        role=user["role"],
        tier=user["tier"],
    )
    refresh_token = create_refresh_token(email=user["email"])

    return LoginResponse(
        data=LoginResponseData(
            user=UserLoginView(
                id=user["id"],
                email=user["email"],
                fullName=user["full_name"],
                role=user["role"],
                subscription=UserSubscriptionView(
                    tier=user["tier"],
                    creditsRemaining=user["credits_remaining"],
                ),
            ),
            token=TokenData(
                accessToken=access_token,
                refreshToken=refresh_token,
                expiresIn=_expires_in_seconds(),
            ),
        ),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(request: RefreshRequest):
    """Exchange a valid refresh token for a new access + refresh pair."""
    try:
        payload = decode_token(request.refreshToken, verify_type=TokenType.REFRESH)
    except Exception as e:
        logger.warning("Refresh token invalid: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = await user_service.get_by_email(payload["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    access_token = create_access_token(
        email=user["email"],
        role=user["role"],
        tier=user["tier"],
    )
    new_refresh_token = create_refresh_token(email=user["email"])

    return RefreshResponse(
        data=RefreshResponseData(
            token=TokenData(
                accessToken=access_token,
                refreshToken=new_refresh_token,
                expiresIn=_expires_in_seconds(),
            ),
        ),
    )


@router.post(
    "/password/request-code", response_model=PasswordResetCodeResponse
)
async def request_password_reset_code(request: PasswordResetCodeRequest):
    """Issue a 6-digit reset code (10-min expiry).

    Always returns 200 to avoid email-enumeration. Until email delivery is
    wired in (B3.6c), the code is returned inline as `devCode` so the
    frontend can complete the reset flow end-to-end during dev.
    """
    result = await auth_service.request_password_reset_code(str(request.email))
    return PasswordResetCodeResponse(
        data=PasswordResetCodeResponseData(
            expiresIn=result["expires_in_seconds"],
            devCode=result["code"],
        ),
    )


@router.post("/password/reset", response_model=PasswordResetResponse)
async def reset_password(request: PasswordResetRequest):
    """Replace password using a code obtained from /password/request-code."""
    try:
        await auth_service.reset_password(
            email=str(request.email),
            code=request.code,
            new_password=request.newPassword,
        )
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return PasswordResetResponse(data=PasswordResetResponseData())


@router.post("/verify", response_model=VerifyResponse)
async def verify(current_user: dict = Depends(get_current_user)):
    """Verify an access token (Bearer). Returns claims if valid, 401 otherwise.

    - **Authorization**: Bearer access token required.
    """
    return VerifyResponse(
        data=VerifyResponseData(
            valid=True,
            user=VerifyUser(
                email=current_user["sub"],
                role=current_user.get("role", ""),
                tier=current_user.get("tier", "free"),
            ),
        ),
    )
