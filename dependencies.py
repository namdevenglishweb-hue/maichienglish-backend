import logging
from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from utils.jwt_utils import TokenType, decode_token

logger = logging.getLogger(__name__)
oauth2_scheme = HTTPBearer(auto_error=True)


async def get_current_user(
    token: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
) -> dict:
    try:
        return decode_token(token.credentials, verify_type=TokenType.ACCESS)
    except Exception as e:
        logger.warning("Token validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def require_teacher_or_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    if current_user.get("role") not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or admin access required",
        )
    return current_user


async def require_parent(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "parent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parent access required",
        )
    return current_user


def require_subscription_tier(required_tiers: list[str]) -> Callable:
    async def dependency(current_user: dict = Depends(get_current_user)) -> dict:
        tier = current_user.get("tier", "free")
        if tier not in required_tiers:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires one of: {', '.join(required_tiers)}",
            )
        return current_user

    return dependency
