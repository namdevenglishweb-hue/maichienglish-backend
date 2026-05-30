"""Tests for utils/password_utils.py — bcrypt hash/verify, no DB."""

from utils.password_utils import hash_password, verify_password


def test_hash_then_verify_returns_true():
    h = hash_password("correctHorseBatteryStaple")
    assert verify_password("correctHorseBatteryStaple", h) is True


def test_verify_wrong_password_returns_false():
    h = hash_password("right-password")
    assert verify_password("wrong-password", h) is False


def test_each_hash_uses_a_fresh_salt():
    """Two hashes of the same password must differ — bcrypt randomizes salt."""
    h1 = hash_password("samepw")
    h2 = hash_password("samepw")

    assert h1 != h2
    # …but both verify against the original
    assert verify_password("samepw", h1) is True
    assert verify_password("samepw", h2) is True


def test_unicode_password_roundtrip():
    """Vietnamese passwords with diacritics must hash + verify cleanly."""
    pw = "mật khẩu mạnh — ĐiỆn 123!"
    h = hash_password(pw)

    assert verify_password(pw, h) is True
    assert verify_password(pw.lower(), h) is False
