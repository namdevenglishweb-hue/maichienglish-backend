"""Tests for api/admin/schemas.py — UploadRequest cross-field validator.

These are pure Pydantic tests, no DB needed. Validation logic lives in
`UploadRequest._cross_validate` and references shared constants from
`services.storage_service` (EXT_FOR_MIME, ALLOWED_TYPES, SIZE_LIMITS).
"""

import pytest
from pydantic import ValidationError

from api.admin.schemas import UploadRequest
from services.storage_service import SIZE_LIMITS


def test_valid_audio_request_parses():
    req = UploadRequest(
        bucket="audio",
        filename="ket-listening-p5.mp3",
        contentType="audio/mpeg",
        fileSizeBytes=3_500_000,
    )
    assert req.bucket == "audio"
    assert req.contentType == "audio/mpeg"


def test_valid_image_request_parses():
    req = UploadRequest(
        bucket="images",
        filename="form.png",
        contentType="image/png",
        fileSizeBytes=500_000,
    )
    assert req.bucket == "images"


def test_rejects_invalid_bucket_literal():
    """Pydantic's Literal["audio","images"] catches this before our
    cross-validator even runs."""
    with pytest.raises(ValidationError):
        UploadRequest(
            bucket="documents",  # not in Literal
            filename="x.mp3",
            contentType="audio/mpeg",
            fileSizeBytes=1000,
        )


def test_rejects_mime_not_in_bucket_whitelist():
    with pytest.raises(ValidationError) as exc:
        UploadRequest(
            bucket="audio",
            filename="x.png",
            contentType="image/png",  # image MIME for audio bucket
            fileSizeBytes=1000,
        )
    assert "Invalid contentType" in str(exc.value)


def test_rejects_unknown_mime_for_correct_bucket():
    """audio/foo is not in ALLOWED_TYPES — must reject with the bucket
    mismatch message (first failing check)."""
    with pytest.raises(ValidationError) as exc:
        UploadRequest(
            bucket="audio",
            filename="x.foo",
            contentType="audio/foo",
            fileSizeBytes=1000,
        )
    assert "Invalid contentType" in str(exc.value)


def test_rejects_oversized_audio_upload():
    too_big = SIZE_LIMITS["audio"] + 1
    with pytest.raises(ValidationError) as exc:
        UploadRequest(
            bucket="audio",
            filename="huge.mp3",
            contentType="audio/mpeg",
            fileSizeBytes=too_big,
        )
    assert "exceeds limit" in str(exc.value)


def test_rejects_oversized_image_upload():
    too_big = SIZE_LIMITS["images"] + 1
    with pytest.raises(ValidationError) as exc:
        UploadRequest(
            bucket="images",
            filename="huge.png",
            contentType="image/png",
            fileSizeBytes=too_big,
        )
    assert "exceeds limit" in str(exc.value)


def test_rejects_zero_or_negative_file_size():
    """`Field(ge=1)` catches this — guards against FE sending wrong
    metadata (e.g. before file is fully read)."""
    with pytest.raises(ValidationError):
        UploadRequest(
            bucket="audio",
            filename="x.mp3",
            contentType="audio/mpeg",
            fileSizeBytes=0,
        )


def test_size_exactly_at_limit_is_accepted():
    """50MB exact should be OK — the rule is `> limit` reject, not `>=`."""
    req = UploadRequest(
        bucket="audio",
        filename="exact.mp3",
        contentType="audio/mpeg",
        fileSizeBytes=SIZE_LIMITS["audio"],
    )
    assert req.fileSizeBytes == SIZE_LIMITS["audio"]
