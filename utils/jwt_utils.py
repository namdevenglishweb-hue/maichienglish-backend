from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from config.settings import get_settings


class TokenType:
    ACCESS = "access"
    REFRESH = "refresh"


def create_token(
    email: str,
    token_type: str,
    expires_delta: timedelta,
    role: Optional[str] = None,
    tier: Optional[str] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload: dict = {
        "sub": email,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    if role:
        payload["role"] = role
    if tier:
        payload["tier"] = tier
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    email: str,
    role: str,
    tier: str,
    extra_claims: Optional[dict] = None,
) -> str:
    settings = get_settings()
    return create_token(
        email=email,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_exp_minutes),
        role=role,
        tier=tier,
        extra_claims=extra_claims,
    )


def create_refresh_token(email: str) -> str:
    settings = get_settings()
    return create_token(
        email=email,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.jwt_refresh_exp_days),
    )


def decode_token(token: str, verify_type: Optional[str] = None) -> dict:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    if verify_type and payload.get("type") != verify_type:
        raise ValueError(
            f"Invalid token type. Expected {verify_type}, got {payload.get('type')}"
        )
    return payload
