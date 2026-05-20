import logging

from fastapi import APIRouter, Depends, HTTPException, status

from config.settings import get_settings
from dependencies import get_current_user
from services.exceptions import InvalidCredentialsError
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
    RefreshRequest,
    RefreshResponse,
    RefreshResponseData,
    TokenData,
    UserLoginView,
    UserSubscriptionView,
    VerifyResponse,
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


@router.post("/verify", response_model=VerifyResponse)
async def verify(current_user: dict = Depends(get_current_user)):
    """Verify an access token (Bearer). Returns claims if valid, 401 otherwise."""
    return VerifyResponse(
        valid=True,
        user=VerifyUser(
            email=current_user["sub"],
            role=current_user.get("role", ""),
            tier=current_user.get("tier", "free"),
        ),
    )
