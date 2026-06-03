"""Tests for utils/jwt_utils.py — pure crypto, no DB."""

from datetime import timedelta

import jwt
import pytest
from freezegun import freeze_time

from utils.jwt_utils import (
    TokenType,
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
)


def test_access_token_roundtrip_carries_role_and_tier():
    token = create_access_token(
        email="user@example.com",
        role="student",
        tier="basic",
    )
    payload = decode_token(token, verify_type=TokenType.ACCESS)

    assert payload["sub"] == "user@example.com"
    assert payload["type"] == TokenType.ACCESS
    assert payload["role"] == "student"
    assert payload["tier"] == "basic"


def test_refresh_token_has_no_role_or_tier():
    token = create_refresh_token(email="user@example.com")
    payload = decode_token(token, verify_type=TokenType.REFRESH)

    assert payload["sub"] == "user@example.com"
    assert payload["type"] == TokenType.REFRESH
    assert "role" not in payload
    assert "tier" not in payload


def test_decode_rejects_wrong_token_type():
    """Refresh token must not be accepted where access is required.

    This guards the most common JWT confusion: passing a long-lived
    refresh token to a route that expects a short-lived access token.
    """
    refresh = create_refresh_token(email="user@example.com")

    with pytest.raises(ValueError, match="Invalid token type"):
        decode_token(refresh, verify_type=TokenType.ACCESS)


def test_decode_without_type_check_returns_payload_of_either_kind():
    access = create_access_token("a@x.com", role="admin", tier="ultra")
    refresh = create_refresh_token("a@x.com")

    # No verify_type → both decode fine
    assert decode_token(access)["type"] == TokenType.ACCESS
    assert decode_token(refresh)["type"] == TokenType.REFRESH


def test_expired_token_raises_pyjwt_error():
    with freeze_time("2026-01-01 00:00:00"):
        token = create_token(
            email="user@example.com",
            token_type=TokenType.ACCESS,
            expires_delta=timedelta(seconds=1),
            role="student",
            tier="free",
        )

    # Advance past expiry
    with freeze_time("2026-01-01 00:00:30"):
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)


def test_tampered_signature_rejected():
    token = create_access_token("user@example.com", role="student", tier="free")
    head, payload_b64, sig = token.rsplit(".", 2)
    # Flip the FIRST signature char, not the last. An HS256 signature is
    # 32 bytes → 43 base64url chars; the *last* char only carries 4
    # significant bits (the low 2 are unused padding), so flipping it can
    # decode to the same bytes and leave the signature valid. The first
    # char's 6 bits are all significant → mutating it always changes the
    # decoded signature, making this assertion deterministic.
    tampered = f"{head}.{payload_b64}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"

    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(tampered)


def test_extra_claims_merged_into_payload():
    token = create_token(
        email="user@example.com",
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=5),
        extra_claims={"custom_flag": "yes", "user_id": "abc-123"},
    )
    payload = decode_token(token)

    assert payload["custom_flag"] == "yes"
    assert payload["user_id"] == "abc-123"
