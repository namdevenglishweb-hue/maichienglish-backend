"""Tests for services/adapters/supabase_storage.py — mock storage3 client.

We never hit real Supabase. The storage3 SDK is mocked so we can verify:
- env-var preconditions (RuntimeError on missing)
- path generation (UUID + extension derived from contentType, NOT filename)
- public_url construction
- error path passthrough

End-to-end behavior against real Supabase is exercised manually + by FE.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.adapters.supabase_storage import SupabaseStorageAdapter


def _settings(url="https://example.supabase.co", key="service-role-key"):
    return SimpleNamespace(
        supabase_url=url,
        supabase_service_role_key=key,
    )


# ---------------------------------------------------------------------------
# Init preconditions — must raise BEFORE any network call
# ---------------------------------------------------------------------------


def test_init_raises_when_url_missing():
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        SupabaseStorageAdapter(_settings(url=None))


def test_init_raises_when_service_role_key_missing():
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        SupabaseStorageAdapter(_settings(key=None))


def test_init_raises_when_both_empty_string():
    """Empty string is falsy — same precondition path."""
    with pytest.raises(RuntimeError):
        SupabaseStorageAdapter(_settings(url="", key=""))


# ---------------------------------------------------------------------------
# create_signed_upload — path + URL construction
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(monkeypatch):
    """Build adapter with a mocked storage3 client.

    We patch storage3.create_client (imported into the adapter module as
    `create_client`) so __init__ doesn't actually try to construct a
    real client.
    """
    fake_client = MagicMock(name="SyncStorageClient")
    monkeypatch.setattr(
        "services.adapters.supabase_storage.create_client",
        lambda url, headers, is_async: fake_client,
    )
    a = SupabaseStorageAdapter(_settings())
    return a, fake_client


async def test_create_signed_upload_returns_uuid_path_with_correct_ext(adapter):
    a, fake_client = adapter

    # storage3 v2 returns dict with signedUrl/token/path
    fake_client.from_.return_value.create_signed_upload_url.return_value = {
        "signedUrl": "https://example.supabase.co/storage/v1/object/upload/sign/audio/abc.mp3?token=xyz",
        "token": "xyz",
        "path": "ignored-by-adapter",
    }

    result = await a.create_signed_upload(
        bucket="audio",
        content_type="audio/mpeg",
        file_size_bytes=1000,
    )

    # Path = UUID + .mp3 derived from contentType (NOT from a filename).
    assert result.path.endswith(".mp3")
    assert len(result.path) == len("00000000-0000-0000-0000-000000000000.mp3")
    assert result.bucket == "audio"
    assert result.token == "xyz"
    assert result.public_url == (
        f"https://example.supabase.co/storage/v1/object/public/audio/{result.path}"
    )


async def test_create_signed_upload_image_uses_jpg_extension(adapter):
    a, fake_client = adapter
    fake_client.from_.return_value.create_signed_upload_url.return_value = {
        "signedUrl": "https://example.supabase.co/storage/v1/x.jpg?token=t",
        "token": "t",
        "path": "x.jpg",
    }

    result = await a.create_signed_upload(
        bucket="images",
        content_type="image/jpeg",
        file_size_bytes=500,
    )

    assert result.path.endswith(".jpg")
    assert "/public/images/" in result.public_url


async def test_create_signed_upload_handles_snake_case_signed_url_key(adapter):
    """Old storage3 versions returned `signed_url`; the adapter accepts
    either spelling defensively."""
    a, fake_client = adapter
    fake_client.from_.return_value.create_signed_upload_url.return_value = {
        "signed_url": "https://example.supabase.co/storage/v1/audio/x.mp3?token=t",
        "token": "t",
        "path": "x.mp3",
    }

    result = await a.create_signed_upload(
        bucket="audio",
        content_type="audio/mpeg",
        file_size_bytes=1000,
    )
    assert result.upload_url.startswith("https://")


async def test_create_signed_upload_handles_relative_signed_path(adapter):
    """Defensive fallback: if a future SDK returns just a path, the
    adapter prefixes it with `{base}/storage/v1`."""
    a, fake_client = adapter
    fake_client.from_.return_value.create_signed_upload_url.return_value = {
        "signedUrl": "/object/upload/sign/audio/x.mp3?token=t",
        "token": "t",
        "path": "x.mp3",
    }

    result = await a.create_signed_upload(
        bucket="audio",
        content_type="audio/mpeg",
        file_size_bytes=1000,
    )

    assert result.upload_url.startswith(
        "https://example.supabase.co/storage/v1/object/upload/sign/audio/"
    )


async def test_delete_file_forwards_path_in_list(adapter):
    a, fake_client = adapter
    bucket_proxy = fake_client.from_.return_value

    await a.delete_file("audio", "abc.mp3")

    bucket_proxy.remove.assert_called_once_with(["abc.mp3"])
