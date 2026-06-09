"""Supabase Storage adapter — uses `storage3` directly (no umbrella).

We pin `storage3` to a version that doesn't pull `pyiceberg` as a
transitive dep. The full `supabase` umbrella package brings in
`pyiceberg` (data-lake library with a C extension) which fails to build
on `python:3.14-slim` for lack of `libc6-dev` headers — and we don't
use any of the umbrella's non-Storage features anyway.

Sync SDK calls are dispatched via `asyncio.to_thread()` so they don't
block the FastAPI event loop. See `MEDIA_UPLOAD.md` §6.3 for rationale.
"""

import asyncio
import logging
import uuid

from storage3 import SyncStorageClient, create_client

from services.exceptions import ValidationError
from services.storage_service import (
    ALLOWED_TYPES,
    EXT_FOR_MIME,
    SIZE_LIMITS,
    StorageService,
    UploadResult,
)

logger = logging.getLogger(__name__)


class SupabaseStorageAdapter(StorageService):
    def __init__(self, settings) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "Supabase storage adapter requires SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY env vars."
            )
        self.base_url: str = settings.supabase_url.rstrip("/")
        key = settings.supabase_service_role_key
        self.client: SyncStorageClient = create_client(
            f"{self.base_url}/storage/v1",
            {"apikey": key, "Authorization": f"Bearer {key}"},
            is_async=False,
        )

    async def create_signed_upload(
        self,
        bucket: str,
        content_type: str,
        file_size_bytes: int,
    ) -> UploadResult:
        # Extension derived from contentType, not filename — see §8.3.
        ext = EXT_FOR_MIME[content_type]
        path = f"{uuid.uuid4()}{ext}"

        # storage3 returns dict with 'signedUrl', 'signed_url', 'token', 'path'.
        # TTL is fixed at 2h by Supabase, not configurable here.
        result = await asyncio.to_thread(
            self.client.from_(bucket).create_signed_upload_url,
            path,
        )

        signed = result.get("signedUrl") or result.get("signed_url") or ""
        if signed and not signed.startswith("http"):
            signed = f"{self.base_url}/storage/v1{signed}"

        token = result.get("token") or ""
        public_url = f"{self.base_url}/storage/v1/object/public/{bucket}/{path}"

        return UploadResult(
            upload_url=signed,
            public_url=public_url,
            token=token,
            path=path,
            bucket=bucket,
        )

    async def delete_file(self, bucket: str, path: str) -> None:
        await asyncio.to_thread(
            self.client.from_(bucket).remove,
            [path],
        )

    async def upload_bytes(
        self, bucket: str, content_type: str, data: bytes
    ) -> str:
        allowed = ALLOWED_TYPES.get(bucket)
        if allowed is None:
            raise ValidationError(f"Unknown bucket {bucket!r}")
        if content_type not in allowed:
            raise ValidationError(
                f"content_type {content_type!r} not allowed for bucket {bucket!r}"
            )
        limit = SIZE_LIMITS.get(bucket)
        if limit is not None and len(data) > limit:
            raise ValidationError(
                f"image is {len(data)} bytes, over the {limit}-byte limit for {bucket!r}"
            )

        ext = EXT_FOR_MIME[content_type]
        path = f"{uuid.uuid4()}{ext}"
        await asyncio.to_thread(
            self.client.from_(bucket).upload,
            path,
            data,
            {"content-type": content_type},
        )
        return f"{self.base_url}/storage/v1/object/public/{bucket}/{path}"
